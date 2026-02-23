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

def _srt_to_ass(srt_path: str, ass_path: str, video_w: int, video_h: int,
                font_size: int, outline: int, margin_v: int):
    """
    Convert file SRT → ASS với style tùy chỉnh vị trí và font.
    Dùng pure Python, không cần thư viện ngoài.
    Alignment=2 (bottom-center), MarginV tính từ dưới lên.
    """
    def _srt_time_to_ass(ts: str) -> str:
        # SRT: 00:00:01,234  →  ASS: 0:00:01.23
        ts = ts.strip().replace(",", ".")
        h, m, rest = ts.split(":", 2)
        s, ms = rest.split(".")
        ms = ms[:2]  # ASS chỉ dùng 2 chữ số centiseconds
        return f"{int(h)}:{m}:{s}.{ms}"

    ass_header = f"""\ufeff[Script Info]
ScriptType: v4.00+
PlayResX: {video_w}
PlayResY: {video_h}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,{outline},0,2,0,0,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    # Parse SRT
    with open(srt_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    # Tách các block subtitle
    blocks = re.split(r"\n\s*\n", content.strip())
    events = []
    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        # Dòng 0: số thứ tự, dòng 1: timecode, dòng 2+: text
        timecode_line = lines[1]
        if "-->" not in timecode_line:
            continue
        start_raw, end_raw = timecode_line.split("-->")
        start_ass = _srt_time_to_ass(start_raw)
        end_ass   = _srt_time_to_ass(end_raw)
        # Ghép text, nhiều dòng dùng \N trong ASS
        text = r"\N".join(lines[2:])
        # Escape { } để không bị hiểu là ASS override tag
        text = text.replace("{", r"\{").replace("}", r"\}")
        events.append(f"Dialogue: 0,{start_ass},{end_ass},Default,,0,0,0,,{text}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.write("\n".join(events))
        f.write("\n")

    print(f"   📄 ASS: {len(events)} dòng | PlayRes={video_w}x{video_h} | MarginV={margin_v}")


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

def get_video_dimensions(video_path):
    """Lấy chiều rộng và chiều cao video bằng ffprobe"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=s=x:p=0", video_path],
            capture_output=True, text=True, check=True
        )
        w, h = result.stdout.strip().split("x")
        return int(w), int(h)
    except Exception as e:
        print(f"   ⚠️  Không lấy được kích thước video: {e}")
        return None, None

def parse_ffmpeg_progress(line, total_duration):
    """Parse output của FFmpeg để lấy tiến độ"""
    time_match = re.search(r'time=(\d{2}):(\d{2}):(\d{2}\.\d{2})', line)
    if time_match and total_duration:
        hours, minutes, seconds = map(float, time_match.groups())
        current_time = hours * 3600 + minutes * 60 + seconds
        progress = (current_time / total_duration) * 100
        return current_time, min(progress, 100)
    return None, None

def escape_srt_path(path: str) -> str:
    """
    Escape đường dẫn SRT cho FFmpeg subtitles filter.
    - Windows: D:/foo/bar.srt  → D\\:/foo/bar.srt
    - Backslash → forward slash trước, rồi escape colon
    """
    path = path.replace("\\", "/")
    # Escape dấu ':' (ký tự đặc biệt trong FFmpeg filter graph)
    path = path.replace(":", "\\:")
    return path

# --- 5.5. API MIX VIDEO (GHÉP PHIM) ---
@router.post("/api/v1/dubbing/mix-video")
def api_mix(req: MixRequest):
    start_time = time.time()
    Logger.section("GHÉP VIDEO (FFMPEG)")

    extracted_audio_temp = None  # Track temporary file for cleanup
    ass_temp_path = None          # Track temporary ASS subtitle file

    try:
        vid, inst, voice = req.video_input, req.instrumental, req.voice_dub

        # Kiểm tra file
        if not os.path.exists(vid): raise FileNotFoundError(f"Thiếu Video: {vid}")
        if not os.path.exists(voice): raise FileNotFoundError(f"Thiếu Voice: {voice}")

        m_vol = req.music_volume if req.music_volume is not None else DEFAULT_MUSIC_VOLUME

        # Xử lý trường hợp nhạc nền là video gốc
        if inst and os.path.normpath(inst) == os.path.normpath(vid):
            print("   🎵 Phát hiện nhạc nền = video gốc, tự động trích xuất audio...")
            video_dir = os.path.dirname(vid)
            extracted_audio = os.path.join(video_dir, f"extracted_audio_{get_timestamp_str()}.mp3")
            extracted_audio_temp = extracted_audio

            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", vid, "-vn", "-acodec", "libmp3lame",
                     "-b:a", "192k", extracted_audio],
                    capture_output=True, text=True, check=True
                )
                inst = extracted_audio
                print(f"   ✅ Đã trích xuất audio: {extracted_audio}")
            except subprocess.CalledProcessError as e:
                print(f"   ⚠️  Không trích xuất được audio từ video gốc: {e}")
                inst = None
                extracted_audio_temp = None

        has_music = (m_vol > 0) and inst and os.path.exists(inst)

        video_dir = os.path.dirname(vid)
        out_file = os.path.join(video_dir, f"out_vi_{get_timestamp_str()}.mp4")

        # Lấy thời lượng và kích thước video
        print("   📊 Đang phân tích video...")
        total_duration = get_video_duration(vid)
        if total_duration:
            print(f"   ⏱️  Thời lượng video: {total_duration:.2f}s ({int(total_duration//60)}:{int(total_duration%60):02d})")

        video_width, video_height = get_video_dimensions(vid)
        if video_width and video_height:
            print(f"   📐 Kích thước video: {video_width}x{video_height}")

        # ============================================================
        # CHỐNG BẢN QUYỀN - COLOR GRADING (Video)
        # ============================================================
        saturation = round(random.uniform(1.15, 1.35), 2)
        contrast = round(random.uniform(1.08, 1.18), 2)
        brightness = round(random.uniform(0.02, 0.08), 3)
        gamma = round(random.uniform(0.95, 1.05), 2)

        print(f"   🎨 COLOR GRADING:")
        print(f"      • Saturation: {saturation}x")
        print(f"      • Contrast: {contrast}x")
        print(f"      • Brightness: +{brightness}")
        print(f"      • Gamma: {gamma}")

        inputs = []
        filters = []

        # ============================================================
        # PHẦN 1: XỬ LÝ VIDEO
        # ============================================================
        video_chain = f"[0:v]eq=saturation={saturation}:contrast={contrast}:brightness={brightness}:gamma={gamma}"

        if req.remove_logo:
            print("   🛡️  Xóa Logo: BẬT")
            video_chain += f",delogo=x={req.logo_x}:y={req.logo_y}:w={req.logo_w}:h={req.logo_h}"

            # --------------------------------------------------------
            # VIETSUB - convert SRT → ASS rồi burn vào video
            # Dùng file .ass thay vì subtitles filter để tránh lỗi
            # đường dẫn Windows và đảm bảo vị trí chính xác
            # --------------------------------------------------------
            has_subtitle = req.subtitle_path and os.path.exists(req.subtitle_path)
            ass_temp_path = None  # track để cleanup sau

            if has_subtitle:
                print(f"   📝 Vietsub: BẬT - {req.subtitle_path}")

                font_size  = req.subtitle_font_size
                logo_y_val = req.logo_y
                logo_h_val = req.logo_h
                vw = video_width  or 720
                vh = video_height or 1280

                # Alignment=2 (bottom-center): MarginV tính từ DƯỚI lên
                margin_v = (vh - logo_y_val - logo_h_val) + max(0, (logo_h_val - font_size) // 2)
                margin_v = max(0, margin_v)

                print(f"   📐 {vw}x{vh} | logo_y={logo_y_val} logo_h={logo_h_val} font={font_size}")
                print(f"   📐 MarginV = ({vh}-{logo_y_val}-{logo_h_val}) + ({logo_h_val}-{font_size})//2 = {margin_v}")

                # ── Tạo file .ass tạm với style đúng ──────────────────
                ass_temp_path = req.subtitle_path.replace(".srt", "_temp_burn.ass")
                try:
                    _srt_to_ass(
                        srt_path=req.subtitle_path,
                        ass_path=ass_temp_path,
                        video_w=vw,
                        video_h=vh,
                        font_size=font_size,
                        outline=req.subtitle_border_width,
                        margin_v=margin_v,
                    )
                    print(f"   ✅ Đã tạo ASS: {ass_temp_path}")
                except Exception as e:
                    print(f"   ❌ Lỗi tạo ASS: {e}")
                    ass_temp_path = None

                if ass_temp_path and os.path.exists(ass_temp_path):
                    # ass filter dùng đường dẫn forward-slash, escape colon
                    ass_escaped = escape_srt_path(ass_temp_path)
                    video_chain += f",ass='{ass_escaped}'"
                    print(f"   🎬 Đã thêm ASS filter vào chain")
                else:
                    print(f"   ⚠️  Bỏ qua subtitle do lỗi tạo ASS")
            else:
                if req.subtitle_path:
                    print(f"   ⚠️  File SRT không tồn tại: {req.subtitle_path}")
                else:
                    print(f"   📝 Vietsub: TẮT")

            # Watermark text bounce
            if req.branding_text:
                print(f"   💧 Watermark Text: '{req.branding_text}'")

                font_size_wm = 28
                alpha = round(random.uniform(0.25, 0.35), 2)

                speed_x = random.randint(48, 50)
                speed_y = random.randint(48, 50)
                direction_x = random.choice([1, -1])
                direction_y = random.choice([1, -1])

                start_x = random.randint(0, 480)
                start_y = random.randint(0, 480)

                print(f"   📐 Font: {font_size_wm}px | Alpha: {alpha} | Speed: ({speed_x},{speed_y})px/s")
                print(f"   🎯 Start: ({start_x},{start_y}) | Direction: ({direction_x},{direction_y})")

                escaped_text = req.branding_text.replace(':', '\\:').replace("'", "\\'")

                margin = 10
                range_x = f"w-tw-{margin*2}"
                move_x = f"abs(mod({start_x}+{speed_x}*{direction_x}*t\\,2*({range_x}))-({range_x}))+{margin}"
                range_y = f"h-th-{margin*2}"
                move_y = f"abs(mod({start_y}+{speed_y}*{direction_y}*t\\,2*({range_y}))-({range_y}))+{margin}"

                video_chain += f",drawtext=text='{escaped_text}':fontsize={font_size_wm}:fontcolor=white@{alpha}:x='{move_x}':y='{move_y}':shadowcolor=black@0.3:shadowx=2:shadowy=2"

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

                filters.append(f"[{brand_idx}:v]scale=80:80[v_brand]")
                filters.append(f"[v_delogo][v_brand]overlay=x=10:y=10[v_out]")
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

        # ============================================================
        # PHẦN 2: XỬ LÝ AUDIO
        # ============================================================
        if has_music:
            print(f"   🎚️  Chế độ: MIXING (Giọng + Nhạc nền)")
            duck, atk, rel = req.ducking_ratio or 5.0, req.attack_time or 50, req.release_time or 300
            voice_idx = 2
            music_idx = 1

            filters.append(f"[{voice_idx}:a]volume={req.voice_volume or 3.0},lowshelf=g=5:f=100:w=0.5[voice]")
            filters.append(f"[voice]asplit[v_trig][v_mix]")

            music_pitch = round(random.uniform(-0.4, 0.4), 2)
            music_highpass = random.randint(60, 100)
            music_lowpass = random.randint(15000, 18000)

            print(f"   🎵 AUDIO TRANSFORMATION (Music Only):")
            print(f"      • Pitch Shift: {music_pitch:+.2f} semitones")
            print(f"      • High-pass Filter: {music_highpass}Hz")
            print(f"      • Low-pass Filter: {music_lowpass}Hz")

            music_filter = f"[{music_idx}:a]"

            if music_pitch != 0:
                rate_factor = round(2 ** (music_pitch / 12), 4)
                music_filter += f"asetrate=44100*{rate_factor},atempo={1/rate_factor},"

            music_filter += f"highpass=f={music_highpass},"
            music_filter += f"lowpass=f={music_lowpass},"
            music_filter += f"volume={m_vol}[bg]"

            filters.append(music_filter)
            filters.append(f"[bg][v_trig]sidechaincompress=threshold=0.1:ratio={duck}:attack={atk}:release={rel}[bg_duck]")
            filters.append(f"[bg_duck][v_mix]amix=inputs=2:duration=longest[a_out]")
        else:
            print(f"   🎚️  Chế độ: VOICE ONLY (Chỉ giọng đọc)")
            voice_idx = 1
            filters.append(f"[{voice_idx}:a]volume={req.voice_volume or 3.0},lowshelf=g=5:f=100:w=0.5[a_out]")

        filter_complex = ";".join(filters)

        cmd = ["ffmpeg", "-y", "-progress", "pipe:1"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", video_map, "-map", "[a_out]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            out_file
        ]

        print("   ⏳ Đang render FFmpeg...")
        print(f"   🔧 Filter: {filter_complex}")

        print(f"   📹 Video: {vid} ({os.path.getsize(vid)} bytes)")
        print(f"   🎤 Voice: {voice} ({os.path.getsize(voice)} bytes)")
        if has_music:
            print(f"   🎵 Music: {inst} ({os.path.getsize(inst)} bytes)")

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

        if extracted_audio_temp and os.path.exists(extracted_audio_temp):
            try:
                os.remove(extracted_audio_temp)
                print(f"   🗑️  Đã xóa file audio tạm: {extracted_audio_temp}")
            except Exception as e:
                print(f"   ⚠️  Không xóa được file tạm: {e}")

        if ass_temp_path and os.path.exists(ass_temp_path):
            try:
                os.remove(ass_temp_path)
                print(f"   🗑️  Đã xóa file ASS tạm: {ass_temp_path}")
            except Exception as e:
                print(f"   ⚠️  Không xóa được ASS tạm: {e}")

        Logger.success("XỬ LÝ THÀNH CÔNG!", total_time)
        print(f"   ⏱️  Thời gian render: {render_time:.2f}s")
        print(f"   ⏱️  Tổng thời gian: {total_time:.2f}s")
        print(f"   📦 Kích thước file: {os.path.getsize(out_file) / 1024 / 1024:.2f} MB")
        print(f"   👉 File đích: {out_file}")

        return {
            "status": "success",
            "output_file": out_file,
            "color_grading": {
                "saturation": saturation,
                "contrast": contrast,
                "brightness": brightness,
                "gamma": gamma
            },
            "audio_transform": {
                "music_pitch": music_pitch if has_music else None,
                "music_highpass": music_highpass if has_music else None,
                "music_lowpass": music_lowpass if has_music else None
            } if has_music else None,
            "render_time": f"{render_time:.2f}s",
            "total_time": f"{total_time:.2f}s",
            "file_size_mb": f"{os.path.getsize(out_file) / 1024 / 1024:.2f}"
        }

    except subprocess.CalledProcessError as e:
        err_msg = e.stderr if isinstance(e.stderr, str) else str(e)
        print("\n❌ LỖI FFMPEG:\n" + "\n".join(err_msg.splitlines()[-10:]))

        if extracted_audio_temp and os.path.exists(extracted_audio_temp):
            try: os.remove(extracted_audio_temp)
            except: pass

        if ass_temp_path and os.path.exists(ass_temp_path):
            try: os.remove(ass_temp_path)
            except: pass

        raise HTTPException(500, "Lỗi khi chạy FFmpeg")
    except Exception as e:
        Logger.error("Lỗi hệ thống", e)

        if extracted_audio_temp and os.path.exists(extracted_audio_temp):
            try: os.remove(extracted_audio_temp)
            except: pass

        if ass_temp_path and os.path.exists(ass_temp_path):
            try: os.remove(ass_temp_path)
            except: pass

        raise HTTPException(500, str(e))