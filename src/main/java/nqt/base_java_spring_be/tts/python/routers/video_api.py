import os
import re
import time
import random
import subprocess
from pathlib import Path
from fastapi import APIRouter, HTTPException
from schemas import MixRequest
from config import DEFAULT_MUSIC_VOLUME
from utils import Logger, get_timestamp_str

router = APIRouter()

def get_video_duration(video_path):
    """Lấy thời lượng video bằng ffprobe"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, check=True
        )
        return float(result.stdout.strip())
    except Exception as e:
        print(f"   ⚠️  Không lấy được thời lượng video: {e}")
        return None

def parse_ffmpeg_progress(line, total_duration):
    """Parse output của FFmpeg để lấy tiến độ"""
    # FFmpeg output format: time=00:01:23.45
    time_match = re.search(r'time=(\d{2}):(\d{2}):(\d{2}\.\d{2})', line)
    if time_match and total_duration:
        hours, minutes, seconds = map(float, time_match.groups())
        current_time = hours * 3600 + minutes * 60 + seconds
        progress = (current_time / total_duration) * 100
        return current_time, min(progress, 100)
    return None, None

# --- 5.5. API MIX VIDEO (GHÉP PHIM) ---
@router.post("/api/v1/dubbing/mix-video")
def api_mix(req: MixRequest):
    start_time = time.time()
    Logger.section("GHÉP VIDEO (FFMPEG)")

    try:
        vid, inst, voice = req.video_input, req.instrumental, req.voice_dub

        # Kiểm tra file
        if not os.path.exists(vid): raise FileNotFoundError(f"Thiếu Video: {vid}")
        if not os.path.exists(voice): raise FileNotFoundError(f"Thiếu Voice: {voice}")

        m_vol = req.music_volume if req.music_volume is not None else DEFAULT_MUSIC_VOLUME
        has_music = (m_vol > 0) and os.path.exists(inst)

        video_dir = os.path.dirname(vid)
        out_file = os.path.join(video_dir, f"out_vi_{get_timestamp_str()}.mp4")

        # Lấy thời lượng video để tính progress
        print("   📊 Đang phân tích video...")
        total_duration = get_video_duration(vid)
        if total_duration:
            print(f"   ⏱️  Thời lượng video: {total_duration:.2f}s ({int(total_duration//60)}:{int(total_duration%60):02d})")

        # Random giảm kích thước để tránh phát hiện bản quyền
        reduce_dimension = random.choice(['width', 'height'])
        reduce_pixels = random.randint(1, 5)
        print(f"   🎲 Random Resize: Giảm {reduce_dimension} đi {reduce_pixels}px")

        # Cấu hình inputs
        inputs = []
        filters = []

        # PHẦN 1: XỬ LÝ VIDEO
        try:
            probe_size = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", vid],
                capture_output=True, text=True, check=True
            )
            orig_w, orig_h = map(int, probe_size.stdout.strip().split(','))

            if reduce_dimension == 'width':
                new_w = orig_w - reduce_pixels
                new_h = orig_h
            else:
                new_w = orig_w
                new_h = orig_h - reduce_pixels

            new_w = new_w if new_w % 2 == 0 else new_w - 1
            new_h = new_h if new_h % 2 == 0 else new_h - 1

            print(f"   📐 Kích thước: {orig_w}x{orig_h} → {new_w}x{new_h}")
            video_chain = f"[0:v]scale={new_w}:{new_h}"
        except Exception as e:
            print(f"   ⚠️  Không lấy được kích thước video: {e}")
            if reduce_dimension == 'width':
                video_chain = f"[0:v]scale=iw-{reduce_pixels}:ih"
            else:
                video_chain = f"[0:v]scale=iw:ih-{reduce_pixels}"

        if req.remove_logo:
            print("   🛡️  Xóa Logo: BẬT")
            video_chain += f",delogo=x={req.logo_x}:y={req.logo_y}:w={req.logo_w}:h={req.logo_h}"

            brand_img_path = req.branding_image_path
            has_branding = brand_img_path and os.path.exists(brand_img_path)

            if has_branding:
                print("   ✅ Chèn Ảnh Thương hiệu: BẬT")
                video_chain += "[v_delogo]"
                filters.append(video_chain)

                inputs = ["-i", vid]
                if has_music:
                    inputs.extend(["-i", inst, "-i", voice, "-i", brand_img_path])
                    brand_idx = 3
                else:
                    inputs.extend(["-i", voice, "-i", brand_img_path])
                    brand_idx = 2

                filters.append(f"[{brand_idx}:v]scale=150:100[v_brand]")
                filters.append(f"[v_delogo][v_brand]overlay=x=0:y=0[v_out]")
                video_map = "[v_out]"
            else:
                print("   ⚠️  Chèn Ảnh Thương hiệu: TẮT")
                video_chain += "[v_out]"
                filters.append(video_chain)
                video_map = "[v_out]"

                inputs = ["-i", vid]
                if has_music:
                    inputs.extend(["-i", inst, "-i", voice])
                else:
                    inputs.extend(["-i", voice])
        else:
            video_chain += "[v_out]"
            filters.append(video_chain)
            video_map = "[v_out]"

            inputs = ["-i", vid]
            if has_music:
                inputs.extend(["-i", inst, "-i", voice])
            else:
                inputs.extend(["-i", voice])

        # PHẦN 2: XỬ LÝ AUDIO
        if has_music:
            print(f"   🎚️  Chế độ: MIXING (Giọng + Nhạc nền)")
            duck, atk, rel = req.ducking_ratio or 5.0, req.attack_time or 50, req.release_time or 300
            voice_idx = 2
            music_idx = 1

            filters.append(f"[{voice_idx}:a]volume={req.voice_volume or 3.0},lowshelf=g=5:f=100:w=0.5[voice]")
            filters.append(f"[voice]asplit[v_trig][v_mix]")
            filters.append(f"[{music_idx}:a]volume={m_vol}[bg]")
            filters.append(f"[bg][v_trig]sidechaincompress=threshold=0.1:ratio={duck}:attack={atk}:release={rel}[bg_duck]")
            filters.append(f"[bg_duck][v_mix]amix=inputs=2:duration=longest[a_out]")
        else:
            print(f"   🎚️  Chế độ: VOICE ONLY (Chỉ giọng đọc)")
            voice_idx = 1
            filters.append(f"[{voice_idx}:a]volume={req.voice_volume or 3.0},lowshelf=g=5:f=100:w=0.5[a_out]")

        filter_complex = ";".join(filters)

        # Tạo lệnh FFmpeg với progress output
        cmd = ["ffmpeg", "-y", "-progress", "pipe:1"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", video_map, "-map", "[a_out]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            out_file
        ]

        print("   ⏳ Đang render FFmpeg...")
        print(f"   🔧 Filter: {filter_complex}")

        # Kiểm tra file input
        print(f"   📹 Video: {vid} ({os.path.getsize(vid)} bytes)")
        print(f"   🎤 Voice: {voice} ({os.path.getsize(voice)} bytes)")
        if has_music:
            print(f"   🎵 Music: {inst} ({os.path.getsize(inst)} bytes)")

        # Chạy FFmpeg với real-time progress tracking
        print("\n" + "="*60)
        render_start = time.time()
        last_progress_update = 0

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )

        stderr_output = []

        # Đọc stderr trong thread riêng để capture errors
        import threading
        def read_stderr():
            for line in process.stderr:
                stderr_output.append(line)

        stderr_thread = threading.Thread(target=read_stderr)
        stderr_thread.daemon = True
        stderr_thread.start()

        # Đọc progress từ stdout
        for line in process.stdout:
            current_time, progress = parse_ffmpeg_progress(line, total_duration)

            if progress is not None:
                elapsed = time.time() - render_start

                # Cập nhật progress mỗi 2% hoặc mỗi 5 giây
                if progress - last_progress_update >= 2 or elapsed - last_progress_update >= 5:
                    if progress > 0:
                        eta = (elapsed / progress * 100) - elapsed
                        print(f"   ⏳ Tiến độ: {progress:5.1f}% | "
                              f"Thời gian: {elapsed:5.1f}s | "
                              f"ETA: ~{eta:5.1f}s")
                    else:
                        print(f"   ⏳ Tiến độ: {progress:5.1f}% | Thời gian: {elapsed:5.1f}s")
                    last_progress_update = progress

        process.wait()
        print("="*60 + "\n")

        if process.returncode != 0:
            print("\n❌ FFMPEG STDERR:")
            print("".join(stderr_output[-20:]))  # In 20 dòng cuối
            raise subprocess.CalledProcessError(
                process.returncode, cmd,
                stderr="".join(stderr_output)
            )

        total_time = time.time() - start_time
        render_time = time.time() - render_start

        Logger.success("XỬ LÝ THÀNH CÔNG!", total_time)
        print(f"   ⏱️  Thời gian render: {render_time:.2f}s")
        print(f"   ⏱️  Tổng thời gian: {total_time:.2f}s")
        print(f"   📦 Kích thước file: {os.path.getsize(out_file) / 1024 / 1024:.2f} MB")
        print(f"   👉 File đích: {out_file}")

        return {
            "status": "success",
            "output_file": out_file,
            "resize_info": f"Giảm {reduce_dimension} đi {reduce_pixels}px",
            "render_time": f"{render_time:.2f}s",
            "total_time": f"{total_time:.2f}s",
            "file_size_mb": f"{os.path.getsize(out_file) / 1024 / 1024:.2f}"
        }

    except subprocess.CalledProcessError as e:
        err_msg = e.stderr if isinstance(e.stderr, str) else str(e)
        print("\n❌ LỖI FFMPEG:\n" + "\n".join(err_msg.splitlines()[-10:]))
        raise HTTPException(500, "Lỗi khi chạy FFmpeg")
    except Exception as e:
        Logger.error("Lỗi hệ thống", e)
        raise HTTPException(500, str(e))