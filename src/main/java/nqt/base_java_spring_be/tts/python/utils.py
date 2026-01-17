# utils.py
import time
import subprocess
import traceback
import sys
import edge_tts
from datetime import datetime

class Logger:
    """Class quản lý việc in log ra màn hình cho đẹp mắt và đồng bộ."""
    @staticmethod
    def info(msg):
        print(f"ℹ️  [THÔNG TIN] {msg}")

    @staticmethod
    def success(msg, elapsed=None):
        time_str = f" ({elapsed:.2f} giây)" if elapsed else ""
        print(f"✅ [THÀNH CÔNG] {msg}{time_str}")

    @staticmethod
    def warning(msg):
        print(f"⚠️  [CẢNH BÁO] {msg}")

    @staticmethod
    def error(msg, exc=None):
        print(f"❌ [LỖI] {msg}")
        if exc:
            print("🔻 CHI TIẾT LỖI (TRACEBACK):")
            traceback.print_exc()

    @staticmethod
    def section(title):
        print(f"\n{'='*60}")
        print(f"🚀 {title.upper()}")
        print(f"{'='*60}")

def get_timestamp_str():
    """Lấy chuỗi thời gian hiện tại để đặt tên file."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def format_timestamp(seconds: float):
    """Chuyển đổi giây sang định dạng SRT (HH:MM:SS,ms)."""
    whole_seconds = int(seconds)
    milliseconds = int((seconds - whole_seconds) * 1000)
    hours = whole_seconds // 3600
    minutes = (whole_seconds % 3600) // 60
    seconds = whole_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def write_srt_faster(segments, file_path, start_index=1):
    """Ghi danh sách segments ra file SRT."""
    with open(file_path, "w", encoding="utf-8") as f:
        for i, segment in enumerate(segments, start=start_index):
            start_str = format_timestamp(segment.start)
            end_str = format_timestamp(segment.end)
            text = segment.text.strip()
            f.write(f"{i}\n{start_str} --> {end_str}\n{text}\n\n")

def normalize_segment_time(segment, min_duration=0.15):
    """Chuẩn hóa thời gian dựa trên word-timestamps để chính xác hơn."""
    if hasattr(segment, "words") and segment.words:
        start = segment.words[0].start
        end = segment.words[-1].end
        if end - start < min_duration:
            end = start + min_duration
        segment.start = round(start, 3)
        segment.end = round(end, 3)
    return segment

def free_port_windows(port):
    """Tự động tìm và tắt tiến trình đang chiếm dụng cổng (Chỉ Windows)."""
    print(f"\n🧹 [AUTO-KILL] Đang kiểm tra cổng {port}...")
    try:
        result = subprocess.run(f"netstat -ano | findstr :{port}", shell=True, capture_output=True, text=True)
        output = result.stdout.strip()

        if not output:
            print(f"   ✅ Cổng {port} đang rảnh. Tiếp tục...")
            return

        pids = set()
        for line in output.split('\n'):
            if "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                pids.add(pid)

        if not pids:
            print(f"   ✅ Không tìm thấy tiến trình LISTENING nào.")
            return

        for pid in pids:
            if pid != "0": # 0 là System Idle
                print(f"   🔪 Đang tắt tiến trình PID {pid} để giải phóng cổng...")
                subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
                print(f"   ✅ Đã tắt PID {pid}.")
        time.sleep(1)

    except Exception as e:
        print(f"⚠️ Không thể tự động giải phóng cổng: {e}")
        print("   -> Vui lòng tắt thủ công nếu gặp lỗi.")

async def generate_tts(text, voice, output_file, rate="+0%"):
    """Gọi Edge-TTS để tạo file âm thanh từ văn bản."""
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_file)