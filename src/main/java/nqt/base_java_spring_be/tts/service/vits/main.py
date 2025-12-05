import io
import json
import os
import re
import torch
import scipy.io.wavfile
import uvicorn
import numpy as np
import math
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from transformers import VitsModel
from torch import nn
from dataclasses import dataclass
from typing import Optional, Tuple

# --- CẤU HÌNH ---
CONFIG = {
    "MODEL_NAME": "facebook/mms-tts-vie",
    "VOCAB_PATH": "vocab.json",
    "DEVICE": "cuda" if torch.cuda.is_available() else "cpu",
    "SAMPLE_RATE": 16000  # MMS thường là 16k
}

# 1. TỰ ĐỊNH NGHĨA OUTPUT CLASS
@dataclass
class VitsTextEncoderOutput:
    last_hidden_state: torch.Tensor
    prior_means: torch.Tensor
    prior_log_variances: torch.Tensor
    hidden_states: Optional[Tuple[torch.Tensor]] = None
    attentions: Optional[Tuple[torch.Tensor]] = None

# 2. CÁC THÀNH PHẦN CỦA ENCODER (Multi-Head Attention & FFN)
class MultiHeadAttention(nn.Module):
    def __init__(self, hidden_channels, heads, p_dropout=0.1):
        super().__init__()
        assert hidden_channels % heads == 0
        self.hidden_channels = hidden_channels
        self.heads = heads
        self.head_dim = hidden_channels // heads

        self.q_proj = nn.Linear(hidden_channels, hidden_channels)
        self.k_proj = nn.Linear(hidden_channels, hidden_channels)
        self.v_proj = nn.Linear(hidden_channels, hidden_channels)
        self.out_proj = nn.Linear(hidden_channels, hidden_channels)
        self.dropout = nn.Dropout(p_dropout)
        self.scale = self.head_dim ** -0.5

    def forward(self, x, mask=None):
        # x: [Batch, Length, Hidden]
        B, L, H = x.shape

        q = self.q_proj(x).view(B, L, self.heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(B, L, self.heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(B, L, self.heads, self.head_dim).transpose(1, 2)

        # Attention Score
        scores = torch.matmul(q, k.transpose(-2, -1)) * self.scale

        if mask is not None:
            # Mask shape [B, L, 1] -> [B, 1, 1, L] để khớp với scores
            mask_expanded = mask.transpose(1, 2).unsqueeze(1)
            scores = scores.masked_fill(mask_expanded == 0, -1e9)

        attn = torch.softmax(scores, dim=-1)
        attn = self.dropout(attn)

        out = torch.matmul(attn, v) # [B, Heads, L, Head_Dim]
        out = out.transpose(1, 2).reshape(B, L, H)
        return self.out_proj(out)

class FeedForward(nn.Module):
    def __init__(self, hidden_channels, filter_channels, kernel_size=3, p_dropout=0.1):
        super().__init__()
        self.conv1 = nn.Conv1d(hidden_channels, filter_channels, kernel_size, padding=kernel_size//2)
        self.conv2 = nn.Conv1d(filter_channels, hidden_channels, kernel_size, padding=kernel_size//2)
        self.act = nn.GELU()
        self.dropout = nn.Dropout(p_dropout)
        self.norm = nn.LayerNorm(hidden_channels)

    def forward(self, x):
        # x: [Batch, Length, Hidden] -> Conv1d cần [Batch, Hidden, Length]
        residual = x
        x = x.transpose(1, 2)
        x = self.conv1(x)
        x = self.act(x)
        x = self.dropout(x)
        x = self.conv2(x)
        x = x.transpose(1, 2)
        return self.norm(x + residual)

class EncoderLayer(nn.Module):
    def __init__(self, hidden_channels, filter_channels, heads, p_dropout=0.1):
        super().__init__()
        self.attn = MultiHeadAttention(hidden_channels, heads, p_dropout)
        self.norm1 = nn.LayerNorm(hidden_channels)
        self.ffn = FeedForward(hidden_channels, filter_channels, 3, p_dropout)
        self.norm2 = nn.LayerNorm(hidden_channels)

    def forward(self, x, mask):
        residual = x
        x = self.norm1(x)
        x = self.attn(x, mask) + residual
        x = self.norm2(x)
        x = self.ffn(x)
        return x

# 3. TEXT ENCODER CHÍNH (Thay thế model.text_encoder)
class TextEncoder(nn.Module):
    def __init__(
            self,
            vocab_size=200,       # Tùy chỉnh theo vocab.json của MMS
            hidden_channels=192,  # Chuẩn của MMS-TTS
            filter_channels=768,
            heads=2,
            layers=6,
            p_dropout=0.1
    ):
        super().__init__()
        self.hidden_channels = hidden_channels
        self.emb = nn.Embedding(vocab_size, hidden_channels)
        nn.init.normal_(self.emb.weight, 0.0, hidden_channels**-0.5)

        self.layers = nn.ModuleList([
            EncoderLayer(hidden_channels, filter_channels, heads, p_dropout)
            for _ in range(layers)
        ])

        # Projection layer: Chia thành Mean (192) và Log_Variance (192)
        self.proj = nn.Conv1d(hidden_channels, hidden_channels * 2, 1)

    def forward(self, input_ids, padding_mask):
        # 1. Embedding & Scaling
        x = self.emb(input_ids) * math.sqrt(self.hidden_channels)

        # 2. Transformer Encoder Blocks
        for layer in self.layers:
            x = layer(x, padding_mask) # padding_mask shape [B, L, 1]

            # Apply Mask vào output để xóa phần padding
            if padding_mask is not None:
                x = x * padding_mask

        last_hidden_state = x

        # 3. Projection -> Mean & Log Variance
        # Conv1d cần input [B, C, L] nên phải transpose
        x_transposed = x.transpose(1, 2)
        stats = self.proj(x_transposed)
        stats = stats.transpose(1, 2) # Quay lại [B, L, C]

        # Chia đôi tensor: phần đầu là Mean, phần sau là Log Variance
        m, logs = torch.split(stats, self.hidden_channels, dim=-1)

        return VitsTextEncoderOutput(
            last_hidden_state=last_hidden_state,
            prior_means=m,
            prior_log_variances=logs
        )

# --- CLASS XỬ LÝ TEXT & LOGIC VITS ---
class VITSEngine:
    def __init__(self, config):
        self.config = config
        self.device = config["DEVICE"]
        self.vocab_map = {}
        self.pad_id = 0
        self.unk_id = 3
        self.model = None

        self.custom_text_encoder = None

        self._load_vocab()
        self._load_model()
        self._init_custom_encoder()

    def _load_vocab(self):
        path = self.config["VOCAB_PATH"]
        if not os.path.exists(path):
            raise FileNotFoundError(f"LỖI: Không tìm thấy {path}")

        print(f"--- Đang tải Vocab từ {path} ---")
        with open(path, "r", encoding="utf-8") as f:
            self.vocab_map = json.load(f)

        self.pad_id = self.vocab_map.get("<pad>", 0)
        self.unk_id = self.vocab_map.get("<unk>", 3)
        print(f"--- Vocab loaded: {len(self.vocab_map)} tokens ---")

    def _load_model(self):
        print(f"--- Đang tải Model: {self.config['MODEL_NAME']} trên {self.device} ---")
        self.model = VitsModel.from_pretrained(self.config["MODEL_NAME"]).to(self.device)
        self.model.eval()
        # Cập nhật sample rate thực tế từ model config
        self.config["SAMPLE_RATE"] = self.model.config.sampling_rate
        print("--- Model Ready! ---")

    def _init_custom_encoder(self):
        print("--- Đang khởi tạo Custom TextEncoder ---")
        # Cấu hình chuẩn của MMS-TTS Vie
        self.custom_text_encoder = TextEncoder(
            vocab_size=len(self.vocab_map),
            hidden_channels=192,
            filter_channels=768,
            heads=2,
            layers=6,
            p_dropout=0.1
        ).to(self.device)
        self.custom_text_encoder.eval()

    def normalize_text(self, text: str) -> str:
        """Chuẩn hóa văn bản cơ bản: Xóa khoảng trắng thừa, xuống dòng."""
        if not text: return ""
        text = text.lower().strip()
        text = re.sub(r'\s+', ' ', text) # Gộp nhiều dấu cách thành 1
        return text

    def tokenizer(self, text: str):
        """Chuyển text thành tensor input cho VITS (có chèn PAD_ID)."""
        text = self.normalize_text(text)
        char_ids = [self.vocab_map.get(char, self.unk_id) for char in text]

        # Interspersed: chèn 0 xen kẽ [0, A, 0, B, 0]
        interspersed_ids = [self.pad_id]
        for char_id in char_ids:
            interspersed_ids.extend([char_id, self.pad_id])

        input_tensor = torch.LongTensor([interspersed_ids])
        attention_mask = torch.ones_like(input_tensor)

        return {
            "input_ids": input_tensor.to(self.device),
            "attention_mask": attention_mask.to(self.device)
        }

    @torch.inference_mode()
    def inference(self, inputs, noise_scale=0.667, length_scale=1.0, noise_scale_w=0.8):
        model = self.model
        custom_encoder = self.custom_text_encoder

        input_ids = inputs["input_ids"]
        attention_mask = inputs["attention_mask"]

        if input_ids.dim() > 2: input_ids = input_ids.squeeze(1)
        if attention_mask.dim() > 2: attention_mask = attention_mask.squeeze(1)

        # 1. Text Encoder
        # enc_out = model.text_encoder(input_ids=input_ids, padding_mask=attention_mask.unsqueeze(-1))
        enc_out = custom_encoder(input_ids=input_ids, padding_mask=attention_mask.unsqueeze(-1))
        # enc_out = self.text_encoder(input_ids, attention_mask.unsqueeze(-1))
        print(f"enc_out: ", enc_out)
        # text_hidden, prior_means, prior_log_variances = enc_out[0], enc_out[1], enc_out[2]
        text_hidden = enc_out.last_hidden_state
        prior_means = enc_out.prior_means
        prior_log_variances = enc_out.prior_log_variances
        text_hidden = text_hidden.transpose(1, 2)

        # 2. Duration Predictor
        x_mask = attention_mask.unsqueeze(1)

        # --- SỬ DỤNG noise_scale_w TỪ THAM SỐ ---
        logw = model.duration_predictor(text_hidden, x_mask, reverse=True, noise_scale=noise_scale_w)

        # --- SỬ DỤNG length_scale TỪ THAM SỐ ---
        w = torch.exp(logw) * x_mask * length_scale
        w_ceil = torch.ceil(w)

        y_lengths = torch.clamp_min(torch.sum(w_ceil, [1, 2]), 1).long()
        y_mask = torch.zeros((input_ids.shape[0], 1, y_lengths.max()), dtype=text_hidden.dtype, device=self.device)
        for i in range(input_ids.shape[0]):
            y_mask[i, :, :y_lengths[i]] = 1

        # 3. Upsample & Flow
        prior_means = prior_means.transpose(1, 2)
        prior_log_variances = prior_log_variances.transpose(1, 2)

        durations = w_ceil.squeeze().long()
        if durations.dim() == 0: durations = durations.unsqueeze(0)

        m_p_upsampled = torch.repeat_interleave(prior_means, durations, dim=2)
        logs_p_upsampled = torch.repeat_interleave(prior_log_variances, durations, dim=2)

        target_len = y_mask.shape[2]
        current_len = m_p_upsampled.shape[2]
        if current_len > target_len:
            m_p_upsampled = m_p_upsampled[:, :, :target_len]
            logs_p_upsampled = logs_p_upsampled[:, :, :target_len]
        elif current_len < target_len:
            pad_size = target_len - current_len
            m_p_upsampled = torch.nn.functional.pad(m_p_upsampled, (0, pad_size))
            logs_p_upsampled = torch.nn.functional.pad(logs_p_upsampled, (0, pad_size))

        # 4. Decoder
        # --- SỬ DỤNG noise_scale TỪ THAM SỐ ---
        z_p = m_p_upsampled + torch.randn_like(m_p_upsampled) * torch.exp(logs_p_upsampled) * noise_scale
        z = model.flow(z_p, y_mask, reverse=True)
        waveform = model.decoder(z * y_mask)

        return waveform.squeeze().cpu().float().numpy()

# --- KHỞI TẠO APP & ENGINE ---
app = FastAPI(title="Vietnamese VITS TTS Service Optimized")
tts_engine = None

@app.on_event("startup")
def startup_event():
    global tts_engine
    tts_engine = VITSEngine(CONFIG)

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, example="Xin chào, kiểm tra tham số.")

    # length_scale: Tốc độ đọc (Càng nhỏ đọc càng nhanh, càng lớn đọc càng chậm)
    # Default VITS là 1.0
    length_scale: float = Field(1.0, ge=0.5, le=2.0, description="Tốc độ (Speed). <1 là nhanh, >1 là chậm.")

    # noise_scale: Độ biến thiên của giọng (Cảm xúc/Ngẫu nhiên)
    # Thấp (0.3): Giọng đều đều, máy móc. Cao (0.8): Giọng biến thiên nhiều, đôi khi lỗi.
    # Chuẩn: 0.667
    noise_scale: float = Field(0.667, ge=0.1, le=1.0, description="Độ biến thiên/Cảm xúc âm thanh.")

    # noise_scale_w: Độ biến thiên của nhịp điệu (Rhythm stochastic duration predictor)
    # Chuẩn: 0.8
    noise_scale_w: float = Field(0.8, ge=0.1, le=1.0, description="Độ biến thiên nhịp điệu.")

@app.post("/api/v1/tts")
async def generate_speech(req: TTSRequest):
    try:
        inputs = tts_engine.tokenizer(req.text)

        audio_data = tts_engine.inference(
            inputs,
            noise_scale=req.noise_scale,
            length_scale=req.length_scale,
            noise_scale_w=req.noise_scale_w
        )

        max_val = np.abs(audio_data).max()
        if max_val > 1.0: audio_data = audio_data / max_val
        audio_data_int16 = (audio_data * 32767).astype(np.int16)

        buffer = io.BytesIO()
        scipy.io.wavfile.write(buffer, tts_engine.config["SAMPLE_RATE"], audio_data_int16)
        buffer.seek(0)

        return StreamingResponse(buffer, media_type="audio/wav")

    except Exception as e:
        print(f"Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)