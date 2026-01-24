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

@router.post("/api/v1/dubbing/crop-video")
def api_mix(req: MixRequest):
    start_time = time.time()
    Logger.section("GHÉP VIDEO (FFMPEG) - ANTI-COPYRIGHT MODE (CROP)")

    try:
        vid, inst, voice = req.video_input, req.instrumental, req.voice_dub

        # Kiểm tra file
        if not os.path.exists(vid): raise FileNotFoundError(f"Thiếu Video: {vid}")
        if not os.path.exists(voice): raise FileNotFoundError(f"Thiếu Voice: {voice}")

        m_vol = req.music_volume if req.music_volume is not None else DEFAULT_MUSIC_VOLUME
        has_music = (m_vol > 0) and os.path.exists(inst)

        video_dir = os.path.dirname(vid)
        out_file = os.path.join(video_dir, f"out_vi_{get_timestamp_str()}.mp4")

        # Lấy thông tin video
        print("   📊 Đang phân tích video...")
        orig_w, orig_h, total_duration = get_video_info(vid)

        if not orig_w or not orig_h:
            raise Exception("Không thể lấy thông tin kích thước video")

        if total_duration:
            print(f"   ⏱️  Thời lượng video: {total_duration:.2f}s ({int(total_duration//60)}:{int(total_duration%60):02d})")
        print(f"   📐 Kích thước gốc: {orig_w}x{orig_h}")

        # ==================== ANTI-COPYRIGHT PARAMETERS ====================

        # 1. RANDOM CROP PARAMETERS (Quan trọng nhất!)
        crop_percent_w = random.uniform(0.88, 0.94)  # Crop 6-12% chiều rộng
        crop_percent_h = random.uniform(0.88, 0.94)  # Crop 6-12% chiều cao

        # Tính kích thước sau crop
        crop_w = int(orig_w * crop_percent_w)
        crop_h = int(orig_h * crop_percent_h)

        # Đảm bảo chẵn
        crop_w = crop_w if crop_w % 2 == 0 else crop_w - 1
        crop_h = crop_h if crop_h % 2 == 0 else crop_h - 1

        # Random vị trí crop (không phải luôn center)
        max_x = orig_w - crop_w
        max_y = orig_h - crop_h
        crop_x = random.randint(0, max_x) if max_x > 0 else 0
        crop_y = random.randint(0, max_y) if max_y > 0 else 0

        # 2. COLOR GRADING PARAMETERS
        saturation = random.uniform(1.15, 1.35)     # Tăng độ bão hòa 15-35%
        contrast = random.uniform(1.08, 1.18)       # Tăng độ tương phản 8-18%
        brightness = random.uniform(0.02, 0.08)     # Tăng độ sáng 2-8%
        gamma = random.uniform(0.95, 1.05)          # Điều chỉnh gamma

        # 3. AUDIO TRANSFORMATION PARAMETERS (CHỈ CHO NHẠC NỀN)
        music_pitch = random.uniform(-0.4, 0.4)     # Pitch shift music
        music_highpass = random.randint(60, 100)    # High-pass filter cho music
        music_lowpass = random.randint(15000, 18000) # Low-pass filter cho music

        print("   🛡️  ======= CHẾ ĐỘ CHỐNG BẢN QUYỀN (CROP) ======")
        print(f"   ✂️  Crop: {orig_w}x{orig_h} → {crop_w}x{crop_h} ({crop_percent_w:.1%}x{crop_percent_h:.1%})")
        print(f"   📍 Crop position: x={crop_x}, y={crop_y}")
        print(f"   🎨 Color: Sat={saturation:.2f} | Con={contrast:.2f} | Bri=+{brightness:.2f} | Gamma={gamma:.2f}")
        print(f"   🎵 Audio Transform (CHỈ NHẠC NỀN): Pitch={music_pitch:+.2f}st | HP={music_highpass}Hz | LP={music_lowpass}Hz")
        print(f"   🎤 Voice: GIỮ NGUYÊN (không transform)")

        # Cấu hình inputs
        inputs = []
        filters = []

        # ==================== PHẦN 1: XỬ LÝ VIDEO ====================

        # FILTER CHAIN:
        # 1. Crop video
        video_chain = (
            f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
            f"eq=saturation={saturation}:contrast={contrast}:brightness={brightness}:gamma={gamma}"
            f"[v_cropped]"
        )
        filters.append(video_chain)

        # ==================== XỬ LÝ LOGO & BRANDING (MANUAL MODE) ====================
        if req.remove_logo:
            print("   🛡️  Xóa Logo/Text: BẬT (MANUAL MODE với CROP)")

            # Lấy giá trị từ request (tọa độ gốc)
            logo_x_orig = req.logo_x
            logo_y_orig = req.logo_y
            logo_w_orig = req.logo_w
            logo_h_orig = req.logo_h

            print(f"   📍 Logo gốc (trước crop): x={logo_x_orig}, y={logo_y_orig}, w={logo_w_orig}, h={logo_h_orig}")

            # Điều chỉnh tọa độ logo theo crop offset
            logo_x_cropped = logo_x_orig - crop_x
            logo_y_cropped = logo_y_orig - crop_y

            print(f"   📍 Logo sau crop: x={logo_x_cropped}, y={logo_y_cropped}, w={logo_w_orig}, h={logo_h_orig}")

            # Validate để đảm bảo logo vẫn trong khung hình sau crop
            if logo_x_cropped < 0:
                logo_w_orig += logo_x_cropped  # Giảm width nếu bị crop bên trái
                logo_x_cropped = 0
            if logo_y_cropped < 0:
                logo_h_orig += logo_y_cropped  # Giảm height nếu bị crop bên trên
                logo_y_cropped = 0
            if logo_y_cropped + logo_h_orig > crop_h:
                logo_h_orig = crop_h - logo_y_cropped
            if logo_x_cropped + logo_w_orig > crop_w:
                logo_w_orig = crop_w - logo_x_cropped

            # Chỉ áp dụng delogo nếu logo vẫn nằm trong vùng crop
            if logo_w_orig > 0 and logo_h_orig > 0 and logo_x_cropped >= 0 and logo_y_cropped >= 0:
                # Áp dụng delogo
                delogo_chain = (
                    f"[v_cropped]delogo=x={logo_x_cropped}:y={logo_y_cropped}:"
                    f"w={logo_w_orig}:h={logo_h_orig}[v_after_delogo]"
                )
                filters.append(delogo_chain)
                last_video_label = "v_after_delogo"
                print(f"   ✅ Đã áp dụng delogo tại vùng crop-adjusted")
            else:
                print(f"   ⚠️  Logo nằm ngoài vùng crop, bỏ qua delogo")
                last_video_label = "v_cropped"

            # XỬ LÝ BRANDING IMAGE
            brand_img_path = req.branding_image_path
            has_branding = brand_img_path and os.path.exists(brand_img_path)

            if has_branding:
                print("   ✅ Chèn Ảnh Thương hiệu: BẬT")

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
                if last_video_label != "v_cropped":
                    filters[-1] = filters[-1].replace(f"[{last_video_label}]", "[v_out]")
                else:
                    filters[-1] = filters[-1].replace("[v_cropped]", "[v_out]")
                video_map = "[v_out]"

                inputs = ["-i", vid]
                if has_music:
                    inputs.extend(["-i", inst, "-i", voice])
                else:
                    inputs.extend(["-i", voice])
        else:
            # KHÔNG XÓA LOGO
            print("   ⚠️  Xóa Logo/Text: TẮT")
            filters[-1] = filters[-1].replace("[v_cropped]", "[v_out]")
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
                f"lowshelf=g=5:f=100:w=0.5"
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
                f"asetrate=44100*2^({music_pitch}/12),aresample=44100,"
                f"equalizer=f=1000:t=h:w=200:g=-2"
                f"[bg]"
            )
            filters.append(music_filter)

            # DUCKING & MIXING
            filters.append(f"[bg][v_trig]sidechaincompress=threshold=0.1:ratio={duck}:attack={atk}:release={rel}[bg_duck]")
            filters.append(f"[bg_duck][v_mix]amix=inputs=2:duration=longest[a_out]")
        else:
            print(f"   🎚️  Chế độ: VOICE ONLY (Chỉ giọng đọc)")
            voice_idx = 1 if not (req.remove_logo and req.branding_image_path and os.path.exists(req.branding_image_path)) else 1

            # VOICE PROCESSING: GIỮ NGUYÊN
            voice_filter = (
                f"[{voice_idx}:a]"
                f"volume={req.voice_volume or 3.0},"
                f"lowshelf=g=5:f=100:w=0.5"
                f"[a_out]"
            )
            filters.append(voice_filter)

        filter_complex = ";".join(filters)

        # Tạo lệnh FFmpeg
        cmd = ["ffmpeg", "-y", "-progress", "pipe:1"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", video_map, "-map", "[a_out]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            out_file
        ]

        print("   ⏳ Đang render FFmpeg...")
        print(f"   🔧 Filter (rút gọn): ...{filter_complex[-100:]}")

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

        import threading
        def read_stderr():
            for line in process.stderr:
                stderr_output.append(line)

        stderr_thread = threading.Thread(target=read_stderr)
        stderr_thread.daemon = True
        stderr_thread.start()

        for line in process.stdout:
            current_time, progress = parse_ffmpeg_progress(line, total_duration)

            if progress is not None:
                elapsed = time.time() - render_start

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
            print("".join(stderr_output[-20:]))
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
                "crop_info": {
                    "original": f"{orig_w}x{orig_h}",
                    "cropped": f"{crop_w}x{crop_h}",
                    "position": f"x={crop_x}, y={crop_y}",
                    "crop_percent": f"{crop_percent_w:.1%}x{crop_percent_h:.1%}"
                },
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
                "cropped_coords": f"x={logo_x_cropped if req.remove_logo else 'N/A'}, y={logo_y_cropped if req.remove_logo else 'N/A'}" if req.remove_logo else None
            },
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