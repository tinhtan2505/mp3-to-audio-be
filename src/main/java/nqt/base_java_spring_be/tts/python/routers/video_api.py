import os
import re
import time
import random
import subprocess
import tempfile
from pathlib import Path
from collections import defaultdict
from fastapi import APIRouter, HTTPException
from schemas import MixRequest
from config import DEFAULT_MUSIC_VOLUME
from utils import Logger, get_timestamp_str

router = APIRouter()

# ==================== HELPER FUNCTIONS ====================

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


def get_video_info(video_path):
    """Lấy thông tin video đầy đủ (width, height, duration)"""
    try:
        result = subprocess.run([
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,duration",
            "-of", "csv=p=0", video_path
        ], capture_output=True, text=True, check=True)

        parts = result.stdout.strip().split(',')
        width, height = int(parts[0]), int(parts[1])
        duration = float(parts[2]) if len(parts) > 2 else get_video_duration(video_path)

        return width, height, duration
    except Exception as e:
        print(f"   ⚠️  Lỗi get_video_info: {e}")
        return None, None, None


def parse_ffmpeg_progress(line, total_duration):
    """Parse output của FFmpeg để lấy tiến độ"""
    time_match = re.search(r'time=(\d{2}):(\d{2}):(\d{2}\.\d{2})', line)
    if time_match and total_duration:
        hours, minutes, seconds = map(float, time_match.groups())
        current_time = hours * 3600 + minutes * 60 + seconds
        progress = (current_time / total_duration) * 100
        return current_time, min(progress, 100)
    return None, None


# ==================== API ENDPOINT ====================

@router.post("/api/v1/dubbing/mix-video")
def api_mix(req: MixRequest):
    start_time = time.time()
    Logger.section("GHÉP VIDEO (FFMPEG) - ANTI-COPYRIGHT MODE")

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

        # ==================== ANTI-COPYRIGHT PARAMETERS ====================

        # 1. PICTURE-IN-PIP PARAMETERS (Quan trọng nhất!)
        pip_scale = random.uniform(0.82, 0.88)  # Thu nhỏ video 82-88%
        blur_strength = random.randint(15, 25)  # Độ mờ nền
        pip_padding = random.randint(40, 80)    # Khoảng cách viền

        # 2. COLOR GRADING PARAMETERS
        saturation = random.uniform(1.15, 1.35)     # Tăng độ bão hòa 15-35%
        contrast = random.uniform(1.08, 1.18)       # Tăng độ tương phản 8-18%
        brightness = random.uniform(0.02, 0.08)     # Tăng độ sáng 2-8%
        gamma = random.uniform(0.95, 1.05)          # Điều chỉnh gamma

        # 3. AUDIO TRANSFORMATION PARAMETERS (CHỈ CHO NHẠC NỀN)
        music_pitch = random.uniform(-0.4, 0.4)     # Pitch shift music
        music_highpass = random.randint(60, 100)    # High-pass filter cho music
        music_lowpass = random.randint(15000, 18000) # Low-pass filter cho music

        # 4. RANDOM RESIZE (bổ sung)
        reduce_dimension = random.choice(['width', 'height'])
        reduce_pixels = random.randint(2, 6)

        print("   🛡️  ======= CHẾ ĐỘ CHỐNG BẢN QUYỀN ======")
        print(f"   📺 PiP Scale: {pip_scale:.2%} | Blur: {blur_strength}px | Padding: {pip_padding}px")
        print(f"   🎨 Color: Sat={saturation:.2f} | Con={contrast:.2f} | Bri=+{brightness:.2f} | Gamma={gamma:.2f}")
        print(f"   🎵 Audio Transform (CHỈ NHẠC NỀN): Pitch={music_pitch:+.2f}st | HP={music_highpass}Hz | LP={music_lowpass}Hz")
        print(f"   🎤 Voice: GIỮ NGUYÊN (không transform)")
        print(f"   🎲 Random Resize: Giảm {reduce_dimension} đi {reduce_pixels}px")

        # Cấu hình inputs
        inputs = []
        filters = []

        # ==================== PHẦN 1: XỬ LÝ VIDEO ====================
        try:
            probe_size = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", vid],
                capture_output=True, text=True, check=True
            )
            orig_w, orig_h = map(int, probe_size.stdout.strip().split(','))

            # Tính toán kích thước sau khi resize
            if reduce_dimension == 'width':
                new_w = orig_w - reduce_pixels
                new_h = orig_h
            else:
                new_w = orig_w
                new_h = orig_h - reduce_pixels

            new_w = new_w if new_w % 2 == 0 else new_w - 1
            new_h = new_h if new_h % 2 == 0 else new_h - 1

            # Tính toán kích thước PiP
            pip_w = int(new_w * pip_scale)
            pip_h = int(new_h * pip_scale)
            pip_w = pip_w if pip_w % 2 == 0 else pip_w - 1
            pip_h = pip_h if pip_h % 2 == 0 else pip_h - 1

            # Vị trí PiP (centered)
            pip_x = (new_w - pip_w) // 2
            pip_y = (new_h - pip_h) // 2

            print(f"   📐 Kích thước: {orig_w}x{orig_h} → {new_w}x{new_h}")
            print(f"   📐 PiP: {pip_w}x{pip_h} tại vị trí ({pip_x}, {pip_y})")

            # FILTER CHAIN:
            # 1. Scale + Color Grading cho video chính (PiP)
            video_chain = (
                f"[0:v]scale={new_w}:{new_h},"
                f"eq=saturation={saturation}:contrast={contrast}:brightness={brightness}:gamma={gamma}"
                f"[v_colored]"
            )
            filters.append(video_chain)

            # 2. Tạo nền mờ từ video gốc
            bg_chain = (
                f"[v_colored]scale={new_w}:{new_h},"
                f"gblur=sigma={blur_strength},"
                f"eq=brightness=-0.1:contrast=0.8"  # Làm tối nền một chút
                f"[v_bg_blur]"
            )
            filters.append(bg_chain)

            # 3. Scale video PiP
            pip_chain = f"[v_colored]scale={pip_w}:{pip_h}[v_pip]"
            filters.append(pip_chain)

            # 4. Overlay PiP lên nền mờ
            overlay_base = f"[v_bg_blur][v_pip]overlay={pip_x}:{pip_y}"

        except Exception as e:
            print(f"   ⚠️  Không lấy được kích thước video: {e}")
            # Fallback với scale động
            video_chain = (
                f"[0:v]scale=iw-{reduce_pixels}:ih,"
                f"eq=saturation={saturation}:contrast={contrast}:brightness={brightness}:gamma={gamma}"
                f"[v_colored]"
            )
            filters.append(video_chain)

            bg_chain = f"[v_colored]gblur=sigma={blur_strength}[v_bg_blur]"
            filters.append(bg_chain)

            pip_chain = f"[v_colored]scale=iw*{pip_scale}:ih*{pip_scale}[v_pip]"
            filters.append(pip_chain)

            overlay_base = f"[v_bg_blur][v_pip]overlay=(W-w)/2:(H-h)/2"

        # ==================== XỬ LÝ LOGO & BRANDING (MANUAL MODE) ====================
        if req.remove_logo:
            print("   🛡️  Xóa Logo/Text: BẬT (MANUAL MODE)")

            # Lấy giá trị từ request (giá trị gốc chưa scale)
            logo_x_orig = req.logo_x
            logo_y_orig = req.logo_y
            logo_w_orig = req.logo_w
            logo_h_orig = req.logo_h

            print(f"   📍 Logo gốc: x={logo_x_orig}, y={logo_y_orig}, w={logo_w_orig}, h={logo_h_orig}")

            # Scale theo tỷ lệ PiP
            logo_x_scaled = int(logo_x_orig * pip_scale) + pip_x
            logo_y_scaled = int(logo_y_orig * pip_scale) + pip_y
            logo_w_scaled = int(logo_w_orig * pip_scale)
            logo_h_scaled = int(logo_h_orig * pip_scale)

            print(f"   📍 Logo scaled: x={logo_x_scaled}, y={logo_y_scaled}, w={logo_w_scaled}, h={logo_h_scaled}")

            # Validate để đảm bảo không vượt khung hình
            if logo_y_scaled < 0:
                logo_y_scaled = 0
            if logo_x_scaled < 0:
                logo_x_scaled = 0
            if logo_y_scaled + logo_h_scaled > new_h:
                logo_h_scaled = new_h - logo_y_scaled
            if logo_x_scaled + logo_w_scaled > new_w:
                logo_w_scaled = new_w - logo_x_scaled

            # Áp dụng delogo
            overlay_base += (
                f"[v_after_overlay];"
                f"[v_after_overlay]delogo=x={logo_x_scaled}:y={logo_y_scaled}:w={logo_w_scaled}:h={logo_h_scaled}"
                f"[v_after_delogo]"
            )
            last_video_label = "v_after_delogo"

            print(f"   ✅ Đã áp dụng delogo tại vùng scaled")

            # XỬ LÝ BRANDING IMAGE
            brand_img_path = req.branding_image_path
            has_branding = brand_img_path and os.path.exists(brand_img_path)

            if has_branding:
                print("   ✅ Chèn Ảnh Thương hiệu: BẬT")
                filters.append(overlay_base)

                inputs = ["-i", vid]
                if has_music:
                    inputs.extend(["-i", inst, "-i", voice, "-i", brand_img_path])
                    brand_idx = 3
                else:
                    inputs.extend(["-i", voice, "-i", brand_img_path])
                    brand_idx = 2

                filters.append(f"[{brand_idx}:v]scale=150:100[v_brand]")
                filters.append(f"[{last_video_label}][v_brand]overlay=x=10:y=10[v_out]")
                video_map = "[v_out]"
            else:
                print("   ⚠️  Chèn Ảnh Thương hiệu: TẮT")
                # Đổi tên label cuối cùng thành v_out
                overlay_base = overlay_base.replace(f"[{last_video_label}]", "[v_out]")
                filters.append(overlay_base)
                video_map = "[v_out]"

                inputs = ["-i", vid]
                if has_music:
                    inputs.extend(["-i", inst, "-i", voice])
                else:
                    inputs.extend(["-i", voice])
        else:
            # KHÔNG XÓA LOGO
            print("   ⚠️  Xóa Logo/Text: TẮT")
            overlay_base += "[v_out]"
            filters.append(overlay_base)
            video_map = "[v_out]"

            inputs = ["-i", vid]
            if has_music:
                inputs.extend(["-i", inst, "-i", voice])
            else:
                inputs.extend(["-i", voice])

        # ==================== PHẦN 2: XỬ LÝ AUDIO ====================
        if has_music:
            print(f"   🎚️  Chế độ: MIXING (Giọng + Nhạc nền) + Audio Transform")
            duck, atk, rel = req.ducking_ratio or 5.0, req.attack_time or 50, req.release_time or 300
            voice_idx = 2 if not (req.remove_logo and req.branding_image_path and os.path.exists(req.branding_image_path)) else 2
            music_idx = 1 if not (req.remove_logo and req.branding_image_path and os.path.exists(req.branding_image_path)) else 1

            # VOICE PROCESSING: GIỮ NGUYÊN - Chỉ Volume + EQ cơ bản
            voice_filter = (
                f"[{voice_idx}:a]"
                f"volume={req.voice_volume or 3.0},"
                f"lowshelf=g=5:f=100:w=0.5"  # Chỉ tăng bass nhẹ cho rõ giọng
                f"[voice]"
            )
            filters.append(voice_filter)
            filters.append(f"[voice]asplit[v_trig][v_mix]")

            # MUSIC PROCESSING: TRANSFORM ĐỂ TRÁNH BẢN QUYỀN
            music_filter = (
                f"[{music_idx}:a]"
                f"volume={m_vol},"
                f"highpass=f={music_highpass},"
                f"lowpass=f={music_lowpass},"
                f"asetrate=44100*2^({music_pitch}/12),aresample=44100,"  # Pitch shift
                f"equalizer=f=1000:t=h:w=200:g=-2"  # Giảm mid để tránh clash với voice
                f"[bg]"
            )
            filters.append(music_filter)

            # DUCKING & MIXING
            filters.append(f"[bg][v_trig]sidechaincompress=threshold=0.1:ratio={duck}:attack={atk}:release={rel}[bg_duck]")
            filters.append(f"[bg_duck][v_mix]amix=inputs=2:duration=longest[a_out]")
        else:
            print(f"   🎚️  Chế độ: VOICE ONLY (Chỉ giọng đọc)")
            voice_idx = 1 if not (req.remove_logo and req.branding_image_path and os.path.exists(req.branding_image_path)) else 1

            # VOICE PROCESSING: GIỮ NGUYÊN - Chỉ Volume + EQ cơ bản
            voice_filter = (
                f"[{voice_idx}:a]"
                f"volume={req.voice_volume or 3.0},"
                f"lowshelf=g=5:f=100:w=0.5"  # Chỉ tăng bass nhẹ cho rõ giọng
                f"[a_out]"
            )
            filters.append(voice_filter)

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
        print(f"   🔧 Filter (rút gọn): ...{filter_complex[-100:]}")

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
            "anti_copyright_applied": {
                "pip_scale": f"{pip_scale:.2%}",
                "blur_strength": blur_strength,
                "color_grading": {
                    "saturation": f"{saturation:.2f}",
                    "contrast": f"{contrast:.2f}",
                    "brightness": f"+{brightness:.2f}",
                    "gamma": f"{gamma:.2f}"
                },
                "audio_transform": {
                    "voice": "UNCHANGED (giữ nguyên)",
                    "music_pitch": f"{music_pitch:+.2f}st",
                    "music_filters": f"HP:{music_highpass}Hz, LP:{music_lowpass}Hz"
                }
            },
            "logo_removal": {
                "enabled": req.remove_logo,
                "original_coords": f"x={req.logo_x}, y={req.logo_y}, w={req.logo_w}, h={req.logo_h}" if req.remove_logo else None,
                "scaled_coords": f"x={logo_x_scaled}, y={logo_y_scaled}, w={logo_w_scaled}, h={logo_h_scaled}" if req.remove_logo else None
            },
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