import whisper
import os
import time

# Kiểm tra file tồn tại chưa
file_name = "mp_tach.wav"
if not os.path.exists(file_name):
    print(f"LỖI: Không tìm thấy file {file_name}")
    exit()

print("--- BƯỚC 1: ĐANG TẢI/LOAD MODEL 'MEDIUM' (Khoảng 1.5GB) ---")
print("Lần đầu chạy sẽ rất lâu, vui lòng không tắt cửa sổ...")
# Load model
start_load = time.time()
model = whisper.load_model("medium")
print(f"-> Load model xong trong {time.time() - start_load:.2f} giây.")

print(f"\n--- BƯỚC 2: ĐANG DỊCH FILE {file_name} ---")
print("Quá trình này rất nặng, CPU sẽ chạy 100%. Vui lòng chờ...")
# Transcribe
start_transcribe = time.time()
result = model.transcribe(file_name, language="zh", fp16=False)
print(f"-> Xử lý xong trong {time.time() - start_transcribe:.2f} giây.")

# Xuất file
print("\n--- BƯỚC 3: XUẤT FILE SRT ---")
from whisper.utils import get_writer
writer = get_writer("srt", ".")
writer(result, "output.srt")
print("-> ĐÃ XONG! Kiểm tra file output.srt trong thư mục.")