import io
import json
import os
import re
import torch
import scipy.io.wavfile
import uvicorn
import numpy as np
from contextlib import asynccontextmanager  # <--- MỚI: Dùng cho lifespan
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from transformers import VitsModel

# --- CẤU HÌNH ---
CONFIG = {
    "MODEL_NAME": "facebook/mms-tts-vie",
    "VOCAB_PATH": "vocab.json",
    "DEVICE": "cuda" if torch.cuda.is_available() else "cpu",
    "SAMPLE_RATE": 16000
}

# --- CLASS XỬ LÝ TEXT & LOGIC VITS (GIỮ NGUYÊN) ---
class VITSEngine:
    def __init__(self, config):
        self.config = config
        self.device = config["DEVICE"]
        self.vocab_map = {}
        self.pad_id = 0
        self.unk_id = 3
        self.model = None

        self._load_vocab()
        self._load_model()

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
        self.config["SAMPLE_RATE"] = self.model.config.sampling_rate
        print("--- Model Ready! ---")

    def normalize_text(self, text: str) -> str:
        if not text: return ""
        text = text.lower().strip()
        text = re.sub(r'\s+', ' ', text)
        return text

    def tokenizer(self, text: str):
        text = self.normalize_text(text)
        char_ids = [self.vocab_map.get(char, self.unk_id) for char in text]

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
        input_ids = inputs["input_ids"]
        attention_mask = inputs["attention_mask"]

        if input_ids.dim() > 2: input_ids = input_ids.squeeze(1)
        if attention_mask.dim() > 2: attention_mask = attention_mask.squeeze(1)

        enc_out = model.text_encoder(input_ids=input_ids, padding_mask=attention_mask.unsqueeze(-1))
        text_hidden, prior_means, prior_log_variances = enc_out[0], enc_out[1], enc_out[2]
        text_hidden = text_hidden.transpose(1, 2)

        x_mask = attention_mask.unsqueeze(1)
        logw = model.duration_predictor(text_hidden, x_mask, reverse=True, noise_scale=noise_scale_w)

        w = torch.exp(logw) * x_mask * length_scale
        w_ceil = torch.ceil(w)

        y_lengths = torch.clamp_min(torch.sum(w_ceil, [1, 2]), 1).long()
        y_mask = torch.zeros((input_ids.shape[0], 1, y_lengths.max()), dtype=text_hidden.dtype, device=self.device)
        for i in range(input_ids.shape[0]):
            y_mask[i, :, :y_lengths[i]] = 1

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

        z_p = m_p_upsampled + torch.randn_like(m_p_upsampled) * torch.exp(logs_p_upsampled) * noise_scale
        z = model.flow(z_p, y_mask, reverse=True)
        waveform = model.decoder(z * y_mask)

        return waveform.squeeze().cpu().float().numpy()

# --- KHỞI TẠO APP & ENGINE (PHẦN ĐÃ SỬA) ---

tts_engine = None

# 1. SỬA: Dùng lifespan thay cho @app.on_event("startup")
@asynccontextmanager
async def lifespan(app: FastAPI):
    global tts_engine
    print("--- LIFESPAN: Đang khởi tạo VITSEngine ---")
    tts_engine = VITSEngine(CONFIG)
    yield
    print("--- LIFESPAN: Ứng dụng đang tắt, dọn dẹp resource ---")
    # Nếu cần giải phóng GPU memory:
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

app = FastAPI(
    title="Vietnamese VITS TTS Service Optimized",
    lifespan=lifespan  # Đăng ký lifespan vào app
)

# 2. SỬA: Cập nhật Pydantic Field (bỏ 'example', dùng 'json_schema_extra')
class TTSRequest(BaseModel):
    text: str = Field(
        ...,
        min_length=1,
        json_schema_extra={"example": "Xin chào, kiểm tra tham số."}
    )

    length_scale: float = Field(
        1.0, ge=0.5, le=2.0,
        description="Tốc độ (Speed). <1 là nhanh, >1 là chậm."
    )

    noise_scale: float = Field(
        0.667, ge=0.1, le=1.0,
        description="Độ biến thiên/Cảm xúc âm thanh."
    )

    noise_scale_w: float = Field(
        0.8, ge=0.1, le=1.0,
        description="Độ biến thiên nhịp điệu."
    )

@app.post("/api/v1/tts")
async def generate_speech(req: TTSRequest):
    if tts_engine is None:
        raise HTTPException(status_code=503, detail="TTS Engine chưa sẵn sàng")

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
    # 3. SỬA: Truyền chuỗi "main:app" để dùng được reload=True mà không warning
    # Đảm bảo tên file của bạn là main.py
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)