import io
import json
import os
import re
import torch
import scipy.io.wavfile
import uvicorn
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from transformers import VitsModel

# --- CẤU HÌNH ---
CONFIG = {
    "MODEL_NAME": "facebook/mms-tts-vie",
    "VOCAB_PATH": "vocab.json",
    "DEVICE": "cuda" if torch.cuda.is_available() else "cpu",
    "SAMPLE_RATE": 16000  # MMS thường là 16k
}

# --- CLASS XỬ LÝ TEXT & LOGIC VITS ---
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
        # Cập nhật sample rate thực tế từ model config
        self.config["SAMPLE_RATE"] = self.model.config.sampling_rate
        print("--- Model Ready! ---")

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
        input_ids = inputs["input_ids"]
        attention_mask = inputs["attention_mask"]

        if input_ids.dim() > 2: input_ids = input_ids.squeeze(1)
        if attention_mask.dim() > 2: attention_mask = attention_mask.squeeze(1)

        # 1. Text Encoder
        enc_out = model.text_encoder(input_ids=input_ids, padding_mask=attention_mask.unsqueeze(-1))
        text_hidden, prior_means, prior_log_variances = enc_out[0], enc_out[1], enc_out[2]
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