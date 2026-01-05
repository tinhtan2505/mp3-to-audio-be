import subprocess
import os

# --- CẤU HÌNH FILE ---
VIDEO_INPUT = "video_goc.mp4"       # Video gốc (chỉ lấy hình)
INSTRUMENTAL = "instrumental.wav"   # File nhạc nền (đã tách lời)
VOICE_DUB = "final_dub.wav"         # File giọng đọc AI (đã có tiếng Việt)
OUTPUT_FILE = "PHIM_LONG_TIENG_PRO.mp4"

# --- CẤU HÌNH ÂM THANH ---
MUSIC_VOLUME = 1.0   # Âm lượng nhạc nền
VOICE_VOLUME = 1.8   # Âm lượng giọng đọc
DUCKING_RATIO = 5    # Độ nén nhạc (càng cao nhạc càng nhỏ khi có người nói)
ATTACK_TIME = 50     # Thời gian bắt đầu nén (ms)
RELEASE_TIME = 300   # Thời gian nhả nén (ms)

def professional_mix_fixed():
    # Kiểm tra file tồn tại
    if not all(os.path.exists(f) for f in [VIDEO_INPUT, INSTRUMENTAL, VOICE_DUB]):
        print("❌ Lỗi: Thiếu file đầu vào! Hãy kiểm tra lại tên file.")
        return

    print("🎧 Đang tiến hành hòa âm chuyên nghiệp (SỬA LỖI MẤT GIỌNG)...")

    # --- GIẢI THÍCH SỬA LỖI ---
    # Lỗi cũ: Dùng [voice_processed] 2 lần -> FFmpeg báo lỗi hoặc mất tiếng.
    # Sửa mới: Thêm lệnh 'asplit' để nhân bản giọng đọc thành 2 luồng:
    #   1. [voice_trigger]: Dùng để ra lệnh nén nhạc.
    #   2. [voice_mix]: Dùng để trộn vào phim.

    filter_complex = (
        # 1. Xử lý giọng đọc (Tăng âm lượng + Bass) -> [voice_proc]
        f"[2:a]volume={VOICE_VOLUME},lowshelf=g=5:f=100:w=0.5[voice_proc];"

        # 2. NHÂN BẢN GIỌNG ĐỌC (Quan trọng nhất)
        f"[voice_proc]asplit[voice_trigger][voice_mix];"

        # 3. Xử lý nhạc nền -> [bg_ready]
        f"[1:a]volume={MUSIC_VOLUME}[bg_ready];"

        # 4. Nén nhạc (Dùng voice_trigger để điều khiển) -> [bg_ducked]
        f"[bg_ready][voice_trigger]sidechaincompress="
        f"threshold=0.1:ratio={DUCKING_RATIO}:attack={ATTACK_TIME}:release={RELEASE_TIME}"
        f"[bg_ducked];"

        # 5. Trộn nhạc đã nén + Giọng đọc (voice_mix) -> [audio_out]
        f"[bg_ducked][voice_mix]amix=inputs=2:duration=longest[audio_out]"
    )

    command = [
        "ffmpeg",
        "-i", VIDEO_INPUT,     # Input 0: Video
        "-i", INSTRUMENTAL,    # Input 1: Nhạc
        "-i", VOICE_DUB,       # Input 2: Giọng
        "-filter_complex", filter_complex,
        "-map", "0:v",         # Lấy Hình Video
        "-map", "[audio_out]", # Lấy Tiếng đã trộn
        "-c:v", "copy",        # Copy hình cho nhanh
        "-y",                  # Ghi đè file cũ
        OUTPUT_FILE
    ]

    try:
        subprocess.run(command, check=True)
        print(f"\n✅ THÀNH CÔNG! File phim hoàn chỉnh: {OUTPUT_FILE}")
    except subprocess.CalledProcessError as e:
        print(f"❌ Có lỗi xảy ra: {e}")

if __name__ == "__main__":
    professional_mix_fixed()