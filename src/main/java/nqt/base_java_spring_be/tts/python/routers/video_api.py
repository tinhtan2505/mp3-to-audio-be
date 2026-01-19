import os
import time
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

        # Cấu hình Audio Filter
        audio_filter = ""
        inputs = []

        if has_music:
            print(f"   🎚️  Chế độ: MIXING (Giọng + Nhạc nền)")
            duck, atk, rel = req.ducking_ratio or 5.0, req.attack_time or 50, req.release_time or 300
            inputs = ["-i", vid, "-i", inst, "-i", voice]
            # Input 0:Video, 1:Music, 2:Voice
            audio_filter = (
                f"[2:a]volume={req.voice_volume or 3.0},lowshelf=g=5:f=100:w=0.5[voice];"
                f"[voice]asplit[v_trig][v_mix];"
                f"[1:a]volume={m_vol}[bg];"
                f"[bg][v_trig]sidechaincompress=threshold=0.1:ratio={duck}:attack={atk}:release={rel}[bg_duck];"
                f"[bg_duck][v_mix]amix=inputs=2:duration=longest[a_out]"
            )
        else:
            print(f"   🎚️  Chế độ: VOICE ONLY (Chỉ giọng đọc)")
            inputs = ["-i", vid, "-i", voice]
            # Input 0:Video, 1:Voice
            audio_filter = f"[1:a]volume={req.voice_volume or 3.0},lowshelf=g=5:f=100:w=0.5[a_out]"

        # Cấu hình Video Filter (Logo)
        video_filter = ""
        video_map = "0:v"
        video_codec = "copy"

        if req.remove_logo:
            print("   🛡️  Xóa Logo: BẬT")

            brand_img_path = req.branding_image_path
            has_branding = brand_img_path and os.path.exists(brand_img_path)

            if has_branding:
                print("   ✅ Chèn Ảnh Thương hiệu: BẬT")
                brand_img_index = int(len(inputs) / 2)
                inputs.extend(["-i", brand_img_path])

                delogo_cmd = f"[0:v]delogo=x={req.logo_x}:y={req.logo_y}:w={req.logo_w}:h={req.logo_h}[v_cl];"

                prepare_img_cmd = (
                    f"[{brand_img_index}:v]"
                    f"scale=150:100[v_img_scaled];"
                )

                overlay_cmd = (
                    f"[v_cl][v_img_scaled]overlay="
                    f"x=0:y=0"
                    f"[v_branded];"
                )

                video_filter = delogo_cmd + prepare_img_cmd + overlay_cmd
                video_map = "[v_branded]"
            else:
                print("   ⚠️  Chèn Ảnh Thương hiệu: TẮT (Không có đường dẫn ảnh hoặc file không tồn tại)")

                delogo_cmd = f"[0:v]delogo=x={req.logo_x}:y={req.logo_y}:w={req.logo_w}:h={req.logo_h}[v_cl]"

                video_filter = delogo_cmd
                video_map = "[v_cl]"

            video_codec = "libx264"

        # Tổng hợp lệnh
        full_filter = (video_filter + audio_filter) if video_filter else audio_filter
        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", full_filter,
            "-map", video_map, "-map", "[a_out]",
            "-c:v", video_codec, "-c:a", "aac", "-b:a", "192k"
        ]
        if video_codec == "libx264": cmd.extend(["-preset", "medium", "-crf", "23"])
        cmd.append(out_file)

        print("   ⏳ Đang render FFmpeg...")
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE)

        Logger.success("XỬ LÝ THÀNH CÔNG!", time.time() - start_time)
        print(f"   👉 File đích: {out_file}")
        return {"status": "success", "output_file": out_file}

    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
        print("\n❌ LỖI FFMPEG:\n" + "\n".join(err_msg.splitlines()[-10:]))
        raise HTTPException(500, "Lỗi khi chạy FFmpeg")
    except Exception as e:
        Logger.error("Lỗi hệ thống", e)
        raise HTTPException(500, str(e))