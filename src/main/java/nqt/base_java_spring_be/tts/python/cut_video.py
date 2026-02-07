import subprocess
import os
import sys

def cut_video(input_file, start_time, duration, output_file=None):
    """
    Cắt video bằng FFmpeg
    
    Args:
        input_file: Đường dẫn file video gốc (ví dụ: 1/video_cn.mp4)
        start_time: Thời điểm bắt đầu (format: HH:MM:SS hoặc MM:SS hoặc giây)
        duration: Độ dài cần cắt (giây)
        output_file: Đường dẫn file đầu ra (ví dụ: 0/video_cn.mp4)
    """
    
    # Kiểm tra file tồn tại
    if not os.path.exists(input_file):
        print(f"❌ Lỗi: File không tồn tại: {input_file}")
        return False
    
    # Tạo tên file output nếu chưa có
    if output_file is None:
        name, ext = os.path.splitext(input_file)
        output_file = f"{name}_cut_{start_time.replace(':', '-')}_{duration}s{ext}"
    
    # Tạo thư mục output nếu chưa tồn tại
    output_dir = os.path.dirname(output_file)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)
        print(f"📁 Đã tạo thư mục: {output_dir}")
    
    # Xóa file cũ nếu đã tồn tại
    if os.path.exists(output_file):
        try:
            os.remove(output_file)
            print(f"🗑️  Đã xóa file cũ: {output_file}")
        except Exception as e:
            print(f"⚠️  Không thể xóa file cũ: {e}")
            return False
    
    # Câu lệnh FFmpeg
    # Đặt -ss TRƯỚC -i để seek chính xác hơn, sau đó re-encode để tránh vấn đề keyframe
    command = [
        'ffmpeg',
        '-ss', start_time,          # Start time (đặt trước -i để seek nhanh)
        '-i', input_file,           # Input file
        '-t', str(duration),        # Duration
        '-c:v', 'libx264',          # Video codec (re-encode để chính xác)
        '-c:a', 'aac',              # Audio codec
        '-preset', 'fast',          # Preset nhanh
        '-avoid_negative_ts', '1',  # Tránh lỗi timestamp âm
        '-y',                       # Overwrite output file
        output_file
    ]
    
    print(f"🎬 Đang cắt video...")
    print(f"   📁 Input: {os.path.basename(input_file)}")
    print(f"   ⏰ Bắt đầu: {start_time}")
    print(f"   ⏱️  Độ dài: {duration} giây")
    print(f"   💾 Output: {os.path.basename(output_file)}")
    print(f"\n🔧 Lệnh FFmpeg: {' '.join(command)}\n")
    
    try:
        # Chạy FFmpeg
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        if result.returncode == 0:
            print(f"✅ Hoàn tất! File đã lưu: {output_file}")
            
            # Hiển thị thông tin file
            if os.path.exists(output_file):
                size_mb = os.path.getsize(output_file) / (1024 * 1024)
                print(f"📊 Kích thước: {size_mb:.2f} MB")
            return True
        else:
            print(f"❌ Lỗi khi cắt video:")
            print(result.stderr)
            return False
            
    except FileNotFoundError:
        print("❌ Lỗi: FFmpeg chưa được cài đặt!")
        print("\n📥 Cách cài FFmpeg:")
        print("   1. Tải từ: https://www.ffmpeg.org/download.html")
        print("   2. Giải nén và thêm vào PATH")
        print("   3. Hoặc dùng: winget install ffmpeg")
        return False
    except Exception as e:
        print(f"❌ Lỗi: {str(e)}")
        return False


# ===== SỬ DỤNG =====
if __name__ == "__main__":
    # CẤU HÌNH - THAY ĐỔI Ở ĐÂY
    INPUT_VIDEO = "1/video_cn.mp4"      # Đường dẫn file video gốc
    START_TIME = "10:00"                # Thời điểm bắt đầu (10 phút 00 giây)
    DURATION = 10                       # Độ dài cần cắt (giây)
    OUTPUT_VIDEO = "0/video_cn.mp4"     # Đường dẫn file đầu ra
    
    print("=" * 60)
    print("🎥 FFMPEG VIDEO CUTTER")
    print("=" * 60)
    
    # Nếu truyền tham số từ command line
    if len(sys.argv) > 1:
        INPUT_VIDEO = sys.argv[1]
        if len(sys.argv) > 2:
            START_TIME = sys.argv[2]
        if len(sys.argv) > 3:
            DURATION = int(sys.argv[3])
        if len(sys.argv) > 4:
            OUTPUT_VIDEO = sys.argv[4]
    
    # Thực hiện cắt video
    success = cut_video(INPUT_VIDEO, START_TIME, DURATION, OUTPUT_VIDEO)
    
    print("=" * 60)
    
    if not success:
        sys.exit(1)


# ===== HƯỚNG DẪN SỬ DỤNG =====
"""
CÁCH 1: Chỉnh sửa biến trong code
    - Sửa INPUT_VIDEO, START_TIME, DURATION ở trên
    - Chạy: python cut_video.py

CÁCH 2: Truyền tham số qua command line
    python cut_video.py 1/video_cn.mp4 10:00 10
    python cut_video.py 1/video_cn.mp4 10:00 10 0/video_cn.mp4

VÍ DỤ SỬ DỤNG:
    # Cắt từ video trong thư mục 1, lưu vào thư mục 0
    python cut_video.py 1/video_cn.mp4 10:00 10 0/video_cn.mp4
    
    # Cắt từ phút 5:30, độ dài 15 giây
    python cut_video.py 1/video_cn.mp4 05:30 15 0/output.mp4

FORMAT THỜI GIAN:
    - HH:MM:SS (ví dụ: 01:30:45)
    - MM:SS (ví dụ: 10:00)
    - Giây (ví dụ: 600)

LƯU Ý:
    - Script tự động tạo thư mục output nếu chưa tồn tại
    - Script tự động xóa file cũ nếu đã tồn tại
    - Sử dụng re-encode để đảm bảo video bắt đầu chính xác từ giây 0
    - Nếu muốn cắt NHANH hơn (có thể bị lệch vài frame):
        Thay '-c:v libx264 -c:a aac -preset fast' 
        bằng '-c copy'
"""