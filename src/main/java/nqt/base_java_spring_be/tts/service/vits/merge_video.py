import os
import subprocess
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

# --- CẤU HÌNH ---
MUSIC_VOLUME = 1.0   # Âm lượng nhạc nền
VOICE_VOLUME = 1.8   # Âm lượng giọng đọc
DUCKING_RATIO = 5    # Độ nén nhạc
ATTACK_TIME = 50     # ms
RELEASE_TIME = 300   # ms

class MixRequest(BaseModel):
    video_input: str    # Video gốc (lấy hình)
    instrumental: str   # Nhạc nền
    voice_dub: str      # Giọng đọc AI

@app.post("/api/v1/mix-video")
def mix_video_process(req: MixRequest):
    video_path = req.video_input
    music_path = req.instrumental
    voice_path = req.voice_dub

    print(f"\n[PORT 8003] Nhận yêu cầu Mix Video:")
    print(f" - Video: {video_path}")
    print(f" - Nhạc : {music_path}")
    print(f" - Voice: {voice_path}")

    # 1. Kiểm tra file input
    if not all(os.path.exists(f) for f in [video_path, music_path, voice_path]):
        raise HTTPException(status_code=400, detail="Một trong các file đầu vào không tồn tại!")

    # 2. Xử lý tên file Output
    # Input: D:\Dubbing\pmh_video_cn.mp4 -> Output: D:\Dubbing\pmh_video_vi.mp4
    output_dir = os.path.dirname(video_path)
    filename_w_ext = os.path.basename(video_path)
    filename_no_ext = os.path.splitext(filename_w_ext)[0]

    # Lấy phần đầu trước dấu "_" (pmh)
    prefix_name = filename_no_ext.split('_')[0]

    output_name = f"{prefix_name}_video_vi.mp4"
    output_full_path = os.path.join(output_dir, output_name)

    # 3. Cấu hình FFmpeg Filter (Sidechain Compression)
    # Logic: Nhân bản giọng đọc ra 2 luồng, 1 luồng để kích hoạt nén nhạc, 1 luồng để trộn.
    filter_complex = (
        f"[2:a]volume={VOICE_VOLUME},lowshelf=g=5:f=100:w=0.5[voice_proc];" # Tăng bass + volume giọng
        f"[voice_proc]asplit[voice_trigger][voice_mix];"                     # Nhân bản giọng
        f"[1:a]volume={MUSIC_VOLUME}[bg_ready];"                             # Chỉnh volume nhạc
        f"[bg_ready][voice_trigger]sidechaincompress="                       # Nén nhạc khi có giọng
        f"threshold=0.1:ratio={DUCKING_RATIO}:attack={ATTACK_TIME}:release={RELEASE_TIME}"
        f"[bg_ducked];"
        f"[bg_ducked][voice_mix]amix=inputs=2:duration=longest[audio_out]"   # Trộn lại
    )

    command = [
        "ffmpeg",
        "-i", video_path,       # Input 0
        "-i", music_path,       # Input 1
        "-i", voice_path,       # Input 2
        "-filter_complex", filter_complex,
        "-map", "0:v",          # Lấy hình từ video gốc
        "-map", "[audio_out]",  # Lấy tiếng đã trộn
        "-c:v", "copy",         # Copy hình (không encode lại -> siêu nhanh)
        "-c:a", "aac",          # Encode tiếng chuẩn AAC
        "-b:a", "192k",
        "-y",                   # Ghi đè
        output_full_path
    ]

    try:
        print("🎧 Đang chạy FFmpeg...")
        # Chạy lệnh (ẩn console window trên Windows nếu cần, ở đây để hiện để debug)
        subprocess.run(command, check=True)
        print(f"✅ XONG! File tại: {output_full_path}")

        return {
            "status": "success",
            "message": "Hòa âm video thành công",
            "output_file": output_full_path
        }

    except subprocess.CalledProcessError as e:
        print(f"❌ Lỗi FFmpeg: {e}")
        raise HTTPException(status_code=500, detail="Lỗi khi chạy FFmpeg mix video")
    except Exception as e:
        print(f"❌ Lỗi hệ thống: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("Mix Server đang chạy tại http://localhost:8003")
    uvicorn.run(app, host="0.0.0.0", port=8003)