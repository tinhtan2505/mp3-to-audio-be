import os
import time
import whisper
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from whisper.utils import get_writer

app = FastAPI()

# --- Load Model ---
print("--- ĐANG LOAD MODEL WHISPER (MEDIUM) ---")
model = whisper.load_model("medium")
print("--- LOAD MODEL THÀNH CÔNG ---")

class DubbingRequest(BaseModel):
    input_path: str

@app.post("/api/v1/dubbing")
def process_dubbing(req: DubbingRequest):
    try:
        input_path = req.input_path
        print(f"\n[NHẬN YÊU CẦU TỪ PORT 8001] Input: {input_path}")

        if not os.path.exists(input_path):
            raise HTTPException(status_code=400, detail=f"File không tồn tại: {input_path}")

        # Xử lý tên file
        output_dir = os.path.dirname(input_path)
        filename_w_ext = os.path.basename(input_path)
        filename_no_ext = os.path.splitext(filename_w_ext)[0]
        prefix_name = filename_no_ext.split('_')[0]
        output_name = f"{prefix_name}_cn"

        # Chạy Whisper
        print(f"-> Đang xử lý Whisper: {input_path}")
        start_time = time.time()
        result = model.transcribe(input_path, language="zh", fp16=False)
        print(f"-> Whisper xong trong {time.time() - start_time:.2f}s")

        # Xuất file SRT
        writer = get_writer("srt", output_dir)
        writer(result, output_name)

        full_output_path = os.path.join(output_dir, output_name + ".srt")
        print(f"-> Đã lưu file tại: {full_output_path}")

        return {
            "status": "success",
            "message": "Đã tạo file SRT thành công",
            "input_file": input_path,
            "output_file": full_output_path
        }

    except Exception as e:
        print(f"LỖI: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # --- SỬA PORT Ở ĐÂY ---
    print("Server đang khởi động tại http://localhost:8001")
    uvicorn.run(app, host="0.0.0.0", port=8001)