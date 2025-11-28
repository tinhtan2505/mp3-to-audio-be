import io
import torch
import numpy as np
import scipy.io.wavfile
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import VitsModel, AutoTokenizer
from fastapi.responses import StreamingResponse
import uvicorn

# Khởi tạo FastAPItôi
app = FastAPI(title="Vietnamese VITS TTS Service")

# --- CẤU HÌNH ---
# Model MMS của Meta hỗ trợ tiếng Việt rất tốt
MODEL_NAME = "facebook/mms-tts-vie"

# Tự động chọn GPU nếu có, không thì dùng CPU
device = "cuda" if torch.cuda.is_available() else "cpu"

print(f"--- Đang khởi tạo VITS Service trên thiết bị: {device} ---")
print(f"--- Đang tải model: {MODEL_NAME} (Lần đầu sẽ hơi lâu) ---")

try:
    # Load model và tokenizer toàn cục (Global) để không phải load lại mỗi request
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = VitsModel.from_pretrained(MODEL_NAME).to(device)
    print("--- Model đã sẵn sàng! ---")
except Exception as e:
    print(f"LỖI NGHIÊM TRỌNG: Không thể tải model. Chi tiết: {e}")
    raise e

class TTSRequest(BaseModel):
    text: str

@app.get("/")
def health_check():
    return {"status": "ok", "message": "VITS Service is running"}

@app.post("/api/v1/tts")
def generate_speech(req: TTSRequest):
    """
    API nhận text và trả về file âm thanh WAV (binary)
    """
    try:
        if not req.text or req.text.strip() == "":
            raise HTTPException(status_code=400, detail="Vui lòng nhập văn bản (text không được rỗng)")

        print(f"Đang xử lý: {req.text}")

        # 1. Tokenize văn bản
        inputs = tokenizer(req.text, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}

        # 2. Chạy Inference (Tắt tính toán gradient để nhẹ máy)
        with torch.no_grad():
            output = model(**inputs).waveform

        # 3. Chuyển đổi dữ liệu Tensor sang dạng mảng NumPy
        audio_data = output.cpu().float().numpy().squeeze()
        sample_rate = model.config.sampling_rate

        # 4. Ghi dữ liệu vào bộ nhớ đệm (RAM) thay vì lưu file cứng
        buffer = io.BytesIO()
        scipy.io.wavfile.write(buffer, rate=sample_rate, data=audio_data)
        buffer.seek(0) # Đưa con trỏ về đầu file để sẵn sàng đọc

        # 5. Trả về stream file wav
        return StreamingResponse(
            buffer,
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=tts_output.wav"}
        )

    except Exception as e:
        print(f"Lỗi khi xử lý TTS: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Chạy server uvicorn tại port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)