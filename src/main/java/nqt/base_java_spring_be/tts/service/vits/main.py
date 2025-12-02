import io
import json
import os
import torch
import scipy.io.wavfile
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from transformers import VitsModel

# --- CẤU HÌNH ---
MODEL_NAME = "facebook/mms-tts-vie"
VOCAB_PATH = "vocab.json"  # File này phải nằm cùng thư mục với main.py

# Tự động chọn GPU nếu có
device = "cuda" if torch.cuda.is_available() else "cpu"

app = FastAPI(title="Vietnamese VITS TTS Service (Manual Tokenizer)")

print(f"--- Đang khởi tạo VITS Service trên thiết bị: {device} ---")

# --- PHẦN 1: LOAD VOCABULARY TỪ FILE JSON LOCAL ---
try:
    if not os.path.exists(VOCAB_PATH):
        raise FileNotFoundError(f"LỖI: Không tìm thấy file '{VOCAB_PATH}'. Hãy đảm bảo file này nằm cùng thư mục code.")

    print(f"--- Đang đọc file từ điển: {VOCAB_PATH} ---")
    with open(VOCAB_PATH, "r", encoding="utf-8") as f:
        vocab_map = json.load(f)

    # Lấy ID của các token đặc biệt
    PAD_ID = vocab_map.get("<pad>", 0)
    UNK_ID = vocab_map.get("<unk>", 3)
    print(f"--- Đã tải {len(vocab_map)} tokens. PAD_ID={PAD_ID}, UNK_ID={UNK_ID} ---")

except Exception as e:
    print(f"LỖI KHỞI TẠO VOCAB: {str(e)}")
    raise e

# --- PHẦN 2: LOAD MODEL (CHỈ LOAD MODEL, KHÔNG LOAD TOKENIZER) ---
print(f"--- Đang tải model AI: {MODEL_NAME} ---")
try:
    # Chỉ tải phần mạng nơ-ron (Model), không tải AutoTokenizer
    model = VitsModel.from_pretrained(MODEL_NAME).to(device)
    model.eval() # Chuyển sang chế độ đánh giá (không train)
    print("--- Model đã sẵn sàng! ---")
except Exception as e:
    print(f"LỖI TẢI MODEL: {str(e)}")
    raise e

# --- PHẦN 3: HÀM TOKENIZER THỦ CÔNG (CUSTOM FUNCTION) ---
def manual_tokenizer_vits(text):
    """
    Chuyển đổi văn bản thành Tensor input cho mô hình VITS.
    Quy tắc:
    1. Chuyển thành ký tự ID dựa trên vocab_map.
    2. Chèn PAD_ID (0) xen kẽ giữa các ký tự (Interspersed).
    """
    if not text:
        return None

    # Chuẩn hóa về chữ thường (vì vocab MMS thường là lowercase)
    text = text.lower()

    char_ids = []

    # Map từng ký tự sang số
    for char in text:
        # Nếu ký tự có trong map thì lấy ID, không thì lấy UNK_ID
        token_id = vocab_map.get(char, UNK_ID)
        char_ids.append(token_id)

    # Chèn số 0 xen kẽ (Bắt buộc với VITS)
    # Ví dụ: [A, B] -> [0, A, 0, B, 0]
    interspersed_ids = [PAD_ID]
    for _id in char_ids:
        interspersed_ids.append(_id)
        interspersed_ids.append(PAD_ID)

    # Tạo Tensor PyTorch
    # Thêm chiều batch (dimension 0) -> shape: [1, sequence_length]
    input_tensor = torch.LongTensor([interspersed_ids])

    # Tạo attention_mask (toàn số 1 vì không padding batch)
    attention_mask = torch.ones_like(input_tensor)

    return {
        "input_ids": input_tensor,
        "attention_mask": attention_mask
    }

class TTSRequest(BaseModel):
    text: str

# --- CÁC ENDPOINT API ---

@app.get("/")
def health_check():
    return {"status": "ok", "message": "VITS Custom Tokenizer Service is running"}

@app.post("/api/v1/tts")
def generate_speech(req: TTSRequest):
    try:
        if not req.text or req.text.strip() == "":
            raise HTTPException(status_code=400, detail="Vui lòng nhập văn bản")

        print(f"Nhận yêu cầu: {req.text}")

        # 1. Sử dụng hàm Tokenizer tự viết
        inputs = manual_tokenizer_vits(req.text)
        print(f"inputs 1: {inputs}")

        # 2. Đưa dữ liệu sang GPU (nếu có)
        inputs = {k: v.to(device) for k, v in inputs.items()}

        # 3. Chạy Inference
        with torch.no_grad():
            output = model(**inputs).waveform

        # 4. Xử lý Output Audio
        audio_data = output.cpu().float().numpy().squeeze()
        sample_rate = model.config.sampling_rate

        # 5. Ghi vào Buffer bộ nhớ
        buffer = io.BytesIO()
        scipy.io.wavfile.write(buffer, rate=sample_rate, data=audio_data)
        buffer.seek(0)

        return StreamingResponse(
            buffer,
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=tts_output.wav"}
        )

    except Exception as e:
        print(f"Lỗi xử lý: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)