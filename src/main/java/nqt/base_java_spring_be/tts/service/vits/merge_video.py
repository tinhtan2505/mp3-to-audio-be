import subprocess
import os

# --- CẤU HÌNH FILE ---
VIDEO_INPUT = "video_goc.mp4"       # Video gốc (chỉ lấy hình)
INSTRUMENTAL = "instrumental.wav"   # File nhạc nền (đã tách lời)
VOICE_DUB = "final_dub.wav"         # File giọng đọc AI
OUTPUT_FILE = "PHIM_LONG_TIENG_PRO.mp4"

# --- CẤU HÌNH ÂM THANH (CHỈNH Ở ĐÂY) ---
# Âm lượng nhạc nền mặc định (khi không có ai nói)
MUSIC_VOLUME = 1.0
# Âm lượng giọng đọc (nên để to rõ)
VOICE_VOLUME = 1.8
# Mức độ "né": Nhạc sẽ giảm đi bao nhiêu lần khi có tiếng nói (3-5 là đẹp)
DUCKING_RATIO = 5
# Tốc độ giảm nhạc (ms): Giảm nhanh (50ms) để không bị đè tiếng
ATTACK_TIME = 50
# Tốc độ hồi phục nhạc (ms): Tăng từ từ (300ms) cho mượt
RELEASE_TIME = 300

def professional_mix():
    # Kiểm tra file
    if not all(os.path.exists(f) for f in [VIDEO_INPUT, INSTRUMENTAL, VOICE_DUB]):
        print("❌ Lỗi: Thiếu file đầu vào! Hãy kiểm tra lại tên file.")
        return

    print("🎧 Đang tiến hành hòa âm chuyên nghiệp (Auto-Ducking + EQ)...")

    # Chuỗi lệnh FFmpeg phức tạp (Filter Complex)
    # 1. [voice_processed]: Tăng âm lượng + Tăng Bass nhẹ (Low shelf) cho giọng ấm hơn
    # 2. [bg_ready]: Chỉnh âm lượng nhạc nền chuẩn bị
    # 3. sidechaincompress: Dùng tín hiệu giọng nói để nén nhạc nền xuống

    filter_complex = (
        f"[2:a]volume={VOICE_VOLUME},lowshelf=g=5:f=100:w=0.5[voice_processed];"
        f"[1:a]volume={MUSIC_VOLUME}[bg_ready];"
        f"[bg_ready][voice_processed]sidechaincompress="
        f"threshold=0.1:ratio={DUCKING_RATIO}:attack={ATTACK_TIME}:release={RELEASE_TIME}"
        f"[bg_ducked];"
        f"[bg_ducked][voice_processed]amix=inputs=2:duration=longest[audio_out]"
    )

    command = [
        "ffmpeg",
        "-i", VIDEO_INPUT,     # Input 0: Video
        "-i", INSTRUMENTAL,    # Input 1: Nhạc nền
        "-i", VOICE_DUB,       # Input 2: Giọng đọc
        "-filter_complex", filter_complex,
        "-map", "0:v",         # Lấy hình ảnh từ Input 0
        "-map", "[audio_out]", # Lấy âm thanh đã trộn
        "-c:v", "copy",        # Copy video cho nhanh (không render lại hình)
        "-y",                  # Tự động ghi đè
        OUTPUT_FILE
    ]

    try:
        subprocess.run(command, check=True)
        print(f"\n✅ XONG! Video chất lượng cao: {OUTPUT_FILE}")
        print("✨ Tính năng nâng cấp: Nhạc tự động nhỏ đi khi có tiếng nói.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Có lỗi xảy ra: {e}")

if __name__ == "__main__":
    professional_mix()