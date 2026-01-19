import os
import time
import random
import subprocess
from pathlib import Path
from fastapi import APIRouter, HTTPException
from schemas import MixRequest
from config import DEFAULT_MUSIC_VOLUME
from utils import Logger, get_timestamp_str

router = APIRouter()

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

        # Random giảm kích thước để tránh phát hiện bản quyền
        # Giảm ngẫu nhiên chiều rộng HOẶC chiều cao từ 1-5px
        reduce_dimension = random.choice(['width', 'height'])
        reduce_pixels = random.randint(1, 5)
        print(f"   🎲 Random Resize: Giảm {reduce_dimension} đi {reduce_pixels}px")

        # Cấu hình inputs
        inputs = []

        # Xây dựng filter_complex
        filters = []

        # PHẦN 1: XỬ LÝ VIDEO
        # Lấy kích thước video gốc
        try:
            probe_size = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height", "-of", "csv=p=0", vid],
                capture_output=True, text=True, check=True
            )
            orig_w, orig_h = map(int, probe_size.stdout.strip().split(','))

            # Giảm chiều rộng hoặc chiều cao
            if reduce_dimension == 'width':
                new_w = orig_w - reduce_pixels
                new_h = orig_h
            else:  # height
                new_w = orig_w
                new_h = orig_h - reduce_pixels

            # Đảm bảo chẵn (yêu cầu của x264)
            new_w = new_w if new_w % 2 == 0 else new_w - 1
            new_h = new_h if new_h % 2 == 0 else new_h - 1

            print(f"   📐 Kích thước: {orig_w}x{orig_h} → {new_w}x{new_h}")
            video_chain = f"[0:v]scale={new_w}:{new_h}"
        except Exception as e:
            print(f"   ⚠️  Không lấy được kích thước video: {e}")
            # Fallback: giảm trực tiếp bằng expression
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

                # Thêm branding image vào inputs
                inputs = ["-i", vid]
                if has_music:
                    inputs.extend(["-i", inst, "-i", voice, "-i", brand_img_path])
                    brand_idx = 3
                else:
                    inputs.extend(["-i", voice, "-i", brand_img_path])
                    brand_idx = 2

                # Scale branding image và overlay
                filters.append(f"[{brand_idx}:v]scale=150:100[v_brand]")
                filters.append(f"[v_delogo][v_brand]overlay=x=0:y=0[v_out]")
                video_map = "[v_out]"
            else:
                print("   ⚠️  Chèn Ảnh Thương hiệu: TẮT")
                video_chain += "[v_out]"
                filters.append(video_chain)
                video_map = "[v_out]"

                # Setup inputs thông thường
                inputs = ["-i", vid]
                if has_music:
                    inputs.extend(["-i", inst, "-i", voice])
                else:
                    inputs.extend(["-i", voice])
        else:
            # Không xóa logo, chỉ scale
            video_chain += "[v_out]"
            filters.append(video_chain)
            video_map = "[v_out]"

            # Setup inputs thông thường
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

        # Ghép tất cả filters
        filter_complex = ";".join(filters)

        # Tạo lệnh FFmpeg
        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", video_map, "-map", "[a_out]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            out_file
        ]

        print("   ⏳ Đang render FFmpeg...")
        print(f"   🔧 Filter: {filter_complex}")

        # Kiểm tra file input trước khi render
        print(f"   📹 Video: {vid} ({os.path.getsize(vid)} bytes)")
        print(f"   🎤 Voice: {voice} ({os.path.getsize(voice)} bytes)")
        if has_music:
            print(f"   🎵 Music: {inst} ({os.path.getsize(inst)} bytes)")

        # Chạy FFmpeg với output đầy đủ để debug
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print("\n❌ FFMPEG STDERR:")
            print(result.stderr)
            raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)

        Logger.success("XỬ LÝ THÀNH CÔNG!", time.time() - start_time)
        print(f"   👉 File đích: {out_file}")
        return {
            "status": "success",
            "output_file": out_file,
            "resize_info": f"Giảm {reduce_dimension} đi {reduce_pixels}px"
        }

    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
        print("\n❌ LỖI FFMPEG:\n" + "\n".join(err_msg.splitlines()[-10:]))
        raise HTTPException(500, "Lỗi khi chạy FFmpeg")
    except Exception as e:
        Logger.error("Lỗi hệ thống", e)
        raise HTTPException(500, str(e))