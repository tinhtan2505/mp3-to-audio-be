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

def manual_vits_inference(model, inputs, noise_scale=0.667, length_scale=1.0, noise_scale_w=0.8):

    # 1. Chuẩn bị inputs
    input_ids = inputs["input_ids"]
    attention_mask = inputs["attention_mask"]

    # Đảm bảo shape là 2 chiều [Batch, Length]
    if input_ids.dim() > 2:
        input_ids = input_ids.squeeze(1)
    if attention_mask.dim() > 2:
        attention_mask = attention_mask.squeeze(1)

    # 2. GỌI TEXT ENCODER (FIX LỖI BROADCASTING)
    # Thêm .unsqueeze(-1) để biến mask từ [1, 435] thành [1, 435, 1]
    # Lúc này: [1, 435, 192] (Embedding) * [1, 435, 1] (Mask) -> OK
    enc_out = model.text_encoder(
        input_ids=input_ids,
        padding_mask=attention_mask.unsqueeze(-1)
    )

    # Lấy các thành phần quan trọng
    # hidden_states: đặc trưng văn bản đã mã hóa
    # x_mask: mặt nạ để che các phần padding
    text_hidden = enc_out[0]
    prior_means = enc_out[1]
    prior_log_variances = enc_out[2]

    text_hidden = text_hidden.transpose(1, 2)

    x_mask = inputs["attention_mask"]
    if x_mask.dim() > 2: x_mask = x_mask.squeeze(1) # Đảm bảo [1, 435]
    x_mask = torch.unsqueeze(x_mask, 1) # -> [1, 1, 435]

    # 2. DURATION PREDICTOR: Dự đoán mỗi token sẽ phát âm trong bao lâu
    # logw: logarit của độ dài (duration)
    logw = model.duration_predictor(
        text_hidden,
        x_mask,
        reverse=True,
        noise_scale=noise_scale_w
    )

    # Chuyển logw thành độ dài thực tế (w)
    # length_scale can thiệp vào đây để chỉnh tốc độ!
    w = torch.exp(logw) * x_mask * length_scale
    w_ceil = torch.ceil(w) # Làm tròn lên số nguyên

    # Tính tổng độ dài (số frame audio sẽ sinh ra)
    y_lengths = torch.clamp_min(torch.sum(w_ceil, [1, 2]), 1).long()
    y_mask = torch.zeros((input_ids.shape[0], 1, y_lengths.max()), dtype=text_hidden.dtype, device=text_hidden.device)

    # Tạo mask cho output audio (y_mask)
    for i in range(input_ids.shape[0]):
        y_mask[i, :, :y_lengths[i]] = 1

    # 3. UPSAMPLING (QUAN TRỌNG): Kéo giãn đặc trưng văn bản khớp với độ dài audio
    # Chúng ta cần lặp lại (repeat) các feature của text dựa trên w_ceil
    # Vì batch=1, ta có thể dùng repeat_interleave cho đơn giản

    # Tạo map để ánh xạ từ text sang audio frame
    # Lưu ý: Đoạn này xử lý tensor khá phức tạp để tương thích GPU,
    # đây là cách implementation chuẩn của VITS để tạo path attention
    attn_mask = torch.unsqueeze(x_mask, 2) * torch.unsqueeze(y_mask, -1)
    prior_means = prior_means.transpose(1, 2)
    prior_log_variances = prior_log_variances.transpose(1, 2)

    # Đây là logic simplified cho Batch Size = 1 (trường hợp API của bạn)
    # Ta tính toán "z" (latent variables) từ phân phối prior (Normal distribution)

    # Tạo m_p và logs_p đã được upsample (kéo giãn)
    # Để đơn giản hóa mà không cần hàm nội bộ phức tạp, ta dùng trick repeat_interleave của PyTorch
    # w_ceil shape: [1, 1, text_len] -> squeeze -> [text_len]
    durations = w_ceil.squeeze().long()

    # Lặp lại mean và variance theo duration dự đoán
    m_p_upsampled = torch.repeat_interleave(prior_means, durations, dim=2)

    logs_p_upsampled = torch.repeat_interleave(prior_log_variances, durations, dim=2)

    # Cắt hoặc pad nếu kích thước không khớp y_mask (do làm tròn số học)
    target_len = y_mask.shape[2]
    current_len = m_p_upsampled.shape[2]

    if current_len > target_len:
        m_p_upsampled = m_p_upsampled[:, :, :target_len]
        logs_p_upsampled = logs_p_upsampled[:, :, :target_len]
    elif current_len < target_len:
        pad_size = target_len - current_len
        m_p_upsampled = torch.nn.functional.pad(m_p_upsampled, (0, pad_size))
        logs_p_upsampled = torch.nn.functional.pad(logs_p_upsampled, (0, pad_size))

    # 4. RANDOMNESS (Latent Variable Generation)
    # Tạo nhiễu ngẫu nhiên (z_p) dựa trên mean và variance
    # noise_scale can thiệp vào đây để chỉnh độ biến thiên giọng!
    z_p = m_p_upsampled + torch.randn_like(m_p_upsampled) * torch.exp(logs_p_upsampled) * noise_scale

    # 5. FLOW (REVERSE): Biến đổi z_p qua Flow Network để có latent phức tạp hơn
    z = model.flow(z_p, y_mask, reverse=True)

    # 6. DECODER (GENERATOR): Sinh sóng âm thanh từ z
    waveform = model.decoder(z * y_mask)

    return waveform

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

        # 2. Đưa dữ liệu sang GPU (nếu có)
        inputs = {k: v.to(device) for k, v in inputs.items()}

        with torch.no_grad():
            # --- TÙY CHỈNH NÂNG CAO ---
            # length_scale=0.8 : Đọc nhanh hơn 20%
            # length_scale=1.2 : Đọc chậm hơn
            # noise_scale=0.667 : Giọng chuẩn
            output = manual_vits_inference(
                model,
                inputs,
                noise_scale=0.667,
                length_scale=1.5,
                noise_scale_w=0.8
            )

        print(f"output_waveform: {output}")

        # 3. Chạy Inference
        # with torch.no_grad():
        #     output = model(**inputs).waveform
        #
        # print(f"output: {output}")
        # 4. Xử lý Output Audio
        audio_data = output.cpu().float().numpy().squeeze()

        # print(f"audio_data: {audio_data}")

        sample_rate = model.config.sampling_rate

        # print(f"sample_rate: {sample_rate}")

        # 5. Ghi vào Buffer bộ nhớ
        buffer = io.BytesIO()
        scipy.io.wavfile.write(buffer, rate=sample_rate, data=audio_data)
        buffer.seek(0)
        # print(f"buffer: {buffer}")

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