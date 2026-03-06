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

DEFAULT_WATERMARK_TEXT = [
    'Link trọn bộ Diu Tút -> Tiểu sử',
    'NQT DRAMA REVIEW'
]

# ============================================================
# CHỐNG BẢN QUYỀN - MODULE NÂNG CAO (v2.0)
# Theo checklist: Remake 40%+ | Đổi màu | Đổi nhạc | Xóa logo
# Thêm: Crop/flip nhẹ | Speed variation | Audio fingerprint bypass
# ============================================================


# ============================================================
# VIETSUB - TÁCH CÂU DÀI (v2.0)
# Tự động chia câu dài thành nhiều dòng ngắn,
# phân bổ thời gian theo tỉ lệ độ dài ký tự từng câu.
# ============================================================

def _split_text_into_sentences(text: str, max_chars: int = 35) -> list:
    """
    Tách text thành các câu ngắn dựa trên dấu câu.
    Thứ tự ưu tiên tách:
      1. \\N  (xuống dòng trong ASS)
      2. !  ?  (dấu kết thúc câu mạnh)
      3. ,   (dấu phẩy - chỉ tách nếu câu vẫn > max_chars)
    """
    # Bước 1: tách theo \\N
    parts = re.split(r'\\N', text)

    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Bước 2: tách theo ! ?
        sub_parts = re.split(r'(?<=[!?])\s+', part)

        for sub in sub_parts:
            sub = sub.strip()
            if not sub:
                continue

            # Bước 3: nếu vẫn dài hơn max_chars → tách theo dấu phẩy
            if len(sub) > max_chars:
                comma_parts = re.split(r'(?<=,)\s+', sub)
                for cp in comma_parts:
                    cp = cp.strip()
                    if cp:
                        result.append(cp)
            else:
                result.append(sub)

    return result if result else [text.strip()]


def _srt_time_to_ms(ts: str) -> int:
    """Chuyển SRT timestamp (HH:MM:SS,mmm) sang milliseconds."""
    ts = ts.strip()
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms)


def _ms_to_ass_time(ms: int) -> str:
    """Chuyển milliseconds sang ASS timestamp (H:MM:SS.cc)."""
    ms = max(0, ms)
    h = ms // 3600000; ms %= 3600000
    m = ms // 60000;   ms %= 60000
    s = ms // 1000;    ms %= 1000
    cs = ms // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _srt_to_ass(srt_path: str, ass_path: str, video_w: int, video_h: int,
                font_size: int, outline: int, margin_v: int):
    """
    Convert file SRT -> ASS với style tùy chỉnh vị trí và font.
    Dùng pure Python, không cần thư viện ngoài.
    Alignment=2 (bottom-center), MarginV tính từ dưới lên.

    TÍNH NĂNG MỚI (v2.0):
      - Tự động tách câu dài thành nhiều dòng ngắn hơn.
      - Phân bổ thời gian hiển thị theo tỉ lệ độ dài ký tự từng câu.
    """
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

    with open(srt_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    blocks = re.split(r"\n\s*\n", content.strip())
    events = []
    split_count = 0

    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 3:
            continue
        timecode_line = lines[1]
        if "-->" not in timecode_line:
            continue

        start_raw, end_raw = timecode_line.split("-->")

        # Chuyển sang ms để tính chia thời gian chính xác
        start_ms = _srt_time_to_ms(start_raw.strip())
        end_ms   = _srt_time_to_ms(end_raw.strip())

        # Gộp text nhiều dòng, escape ký tự đặc biệt ASS
        raw_text = r"\N".join(lines[2:])
        raw_text = raw_text.replace("{", r"\{").replace("}", r"\}")

        # Tách câu dài thành các câu ngắn hơn
        sentences = _split_text_into_sentences(raw_text, max_chars=35)

        if len(sentences) <= 1:
            # Không cần tách - giữ nguyên
            events.append(
                f"Dialogue: 0,{_ms_to_ass_time(start_ms)},{_ms_to_ass_time(end_ms)},"
                f"Default,,0,0,0,,{raw_text}"
            )
        else:
            # Tách và phân bổ thời gian theo tỉ lệ độ dài ký tự
            split_count += 1
            total_chars    = sum(len(s) for s in sentences)
            total_duration = end_ms - start_ms
            current_ms     = start_ms

            for i, sentence in enumerate(sentences):
                char_ratio = len(sentence) / total_chars
                duration   = int(total_duration * char_ratio)
                seg_end_ms = end_ms if i == len(sentences) - 1 else current_ms + duration

                events.append(
                    f"Dialogue: 0,{_ms_to_ass_time(current_ms)},{_ms_to_ass_time(seg_end_ms)},"
                    f"Default,,0,0,0,,{sentence}"
                )
                current_ms = seg_end_ms

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(ass_header)
        f.write("\n".join(events))
        f.write("\n")

    print(f"   📄 ASS: {len(events)} dòng | PlayRes={video_w}x{video_h} | MarginV={margin_v}")
    if split_count > 0:
        print(f"   ✂️  Đã tách {split_count} block câu dài thành nhiều dòng ngắn hơn")


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
    """
    path = path.replace("\\", "/")
    path = path.replace(":", "\\:")
    return path


def build_copyright_bypass_video_chain(base_chain: str, video_width: int, video_height: int) -> tuple[str, dict]:
    """
    ============================================================
    CHỐNG BẢN QUYỀN - XỬ LÝ VIDEO (Remake 40%+)
    Áp dụng các kỹ thuật thay đổi "dấu vân tay" video:
    1. Color grading (đã có) - đổi màu ngẫu nhiên
    2. Crop nhẹ + scale lại - thay đổi tỷ lệ khung hình
    3. Flip ngang ngẫu nhiên (nhẹ - không làm hỏng nội dung)
    4. Speed variation nhẹ (±2%) - thay đổi tốc độ
    5. Noise injection siêu nhẹ - thêm grain để bypass hash
    6. Sharpen / Blur nhẹ - thay đổi texture
    ============================================================
    Returns: (modified_chain, params_dict)
    """
    params = {}

    # --- 1. COLOR GRADING (đổi màu) ---
    saturation  = round(random.uniform(1.15, 1.35), 2)
    contrast    = round(random.uniform(1.08, 1.18), 2)
    brightness  = round(random.uniform(0.02, 0.08), 3)
    gamma       = round(random.uniform(0.95, 1.05), 2)
    params["saturation"]  = saturation
    params["contrast"]    = contrast
    params["brightness"]  = brightness
    params["gamma"]       = gamma

    chain = f"{base_chain}eq=saturation={saturation}:contrast={contrast}:brightness={brightness}:gamma={gamma}"
    print(f"   🎨 COLOR GRADING: sat={saturation} con={contrast} bri={brightness} gam={gamma}")

    # --- 2. CROP NHẸ (2-4%) + scale lại kích thước gốc ---
    if video_width and video_height:
        crop_pct = round(random.uniform(0.02, 0.04), 3)
        crop_w   = int(video_width  * (1 - crop_pct))
        crop_h   = int(video_height * (1 - crop_pct))
        crop_w   = crop_w - (crop_w % 2)
        crop_h   = crop_h - (crop_h % 2)
        crop_x   = (video_width  - crop_w) // 2
        crop_y   = (video_height - crop_h) // 2
        chain   += f",crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={video_width}:{video_height}"
        params["crop_pct"] = crop_pct
        print(f"   ✂️  CROP: {crop_pct*100:.1f}% → {crop_w}x{crop_h} → scale lại {video_width}x{video_height}")

    # --- 3. NOISE INJECTION (grain nhẹ - bypass perceptual hash) ---
    noise_strength = round(random.uniform(1.5, 3.5), 1)
    chain += f",noise=alls={noise_strength}:allf=t+u"
    params["noise_strength"] = noise_strength
    print(f"   🌫️  NOISE: strength={noise_strength} (bypass perceptual hash)")

    # --- 4. SHARPEN nhẹ (tăng độ nét sau noise) ---
    sharpen_luma = round(random.uniform(0.3, 0.8), 2)
    chain += f",unsharp=luma_msize_x=3:luma_msize_y=3:luma_amount={sharpen_luma}"
    params["sharpen"] = sharpen_luma
    print(f"   🔍 SHARPEN: luma_amount={sharpen_luma}")

    # --- 5. HUE SHIFT nhẹ (±3 độ) ---
    hue_shift = round(random.uniform(-3, 3), 1)
    chain    += f",hue=h={hue_shift}"
    params["hue_shift"] = hue_shift
    print(f"   🌈 HUE SHIFT: {hue_shift:+.1f}°")

    return chain, params


def build_music_copyright_bypass(music_input_label: str, m_vol: float) -> tuple[str, dict]:
    """
    ============================================================
    CHỐNG BẢN QUYỀN - XỬ LÝ NHẠC NỀN (Bypass audio fingerprint)
    Voice AI của user giữ nguyên - KHÔNG transform.
    Chỉ transform nhạc nền để bypass Content ID.
    1. Pitch shift (±0.4 semitone)
    2. Tempo variation (±1.5%)
    3. EQ: highpass + lowpass
    4. Volume adjust
    ============================================================
    Returns: (filter_string, params_dict)
    """
    params = {}

    music_pitch    = round(random.uniform(-0.4, 0.4), 2)
    music_tempo    = round(random.uniform(0.985, 1.015), 4)
    music_highpass = random.randint(60, 100)
    music_lowpass  = random.randint(15000, 18000)

    params["music_pitch"]    = music_pitch
    params["music_tempo"]    = music_tempo
    params["music_highpass"] = music_highpass
    params["music_lowpass"]  = music_lowpass

    print(f"   🎵 MUSIC TRANSFORMATION (bypass Content ID):")
    print(f"      • Pitch Shift: {music_pitch:+.2f} semitones")
    print(f"      • Tempo: {music_tempo:.4f}x ({(music_tempo-1)*100:+.1f}%)")
    print(f"      • High-pass: {music_highpass}Hz | Low-pass: {music_lowpass}Hz")
    print(f"   🎤 VOICE: Giữ nguyên (AI voice - không cần transform)")

    chain = f"{music_input_label}"
    if music_pitch != 0:
        rate_factor = round(2 ** (music_pitch / 12), 4)
        chain += f"asetrate=44100*{rate_factor},atempo={round(1/rate_factor, 4)},"
    if abs(music_tempo - 1.0) > 0.001:
        chain += f"atempo={music_tempo},"
    chain += f"highpass=f={music_highpass},lowpass=f={music_lowpass},"
    chain += f"volume={m_vol}[bg]"

    return chain, params


# --- API MIX VIDEO (GHÉP PHIM) ---
@router.post("/api/v1/dubbing/crop-video")
def api_mix(req: MixRequest):
    start_time = time.time()
    Logger.section("GHÉP VIDEO (FFMPEG) - CHỐNG BẢN QUYỀN v2.0")

    extracted_audio_temp = None
    ass_temp_path = None

    try:
        vid, inst, voice = req.video_input, req.instrumental, req.voice_dub

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

        inputs = []
        filters = []

        # ============================================================
        # PHẦN 1: XỬ LÝ VIDEO - CHỐNG BẢN QUYỀN NÂNG CAO
        # ============================================================
        print("\n   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("   🛡️  CHỐNG BẢN QUYỀN - VIDEO TRANSFORMATION")
        print("   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        base_chain = f"[0:v]"
        video_chain, video_transform_params = build_copyright_bypass_video_chain(
            base_chain, video_width, video_height
        )

        saturation   = video_transform_params["saturation"]
        contrast     = video_transform_params["contrast"]
        brightness   = video_transform_params["brightness"]
        gamma        = video_transform_params["gamma"]

        if req.remove_logo:
            print(f"\n   🛡️  Xóa Logo: BẬT (x={req.logo_x}, y={req.logo_y}, w={req.logo_w}, h={req.logo_h})")
            video_chain += f",delogo=x={req.logo_x}:y={req.logo_y}:w={req.logo_w}:h={req.logo_h}"

            # ---- VIETSUB: convert SRT → ASS ----
            has_subtitle = req.subtitle_path and os.path.exists(req.subtitle_path)

            # Nếu không tìm thấy subtitle, tự động tìm file SRT có "vi_FULL" trong tên
            if not has_subtitle:
                video_dir_sub = os.path.dirname(vid)
                found_srt = None
                try:
                    for f in os.listdir(video_dir_sub):
                        if "vi_FULL" in f and f.endswith(".srt"):
                            found_srt = os.path.join(video_dir_sub, f)
                            break
                except Exception as _e:
                    print(f"   ⚠️  Lỗi khi tìm file SRT: {_e}")
                if found_srt:
                    print(f"   🔍 Tự động tìm thấy SRT vi_FULL: {found_srt}")
                    req.subtitle_path = found_srt
                    has_subtitle = True
                else:
                    print(f"   ⚠️  Không tìm thấy file SRT vi_FULL trong: {video_dir_sub}")

            if has_subtitle:
                print(f"   📝 Vietsub: BẬT - {req.subtitle_path}")

                font_size  = req.subtitle_font_size
                logo_y_val = req.logo_y
                logo_h_val = req.logo_h
                vw = video_width  or 720
                vh = video_height or 1280

                margin_v = (vh - logo_y_val - logo_h_val) + max(0, (logo_h_val - font_size) // 2)
                margin_v = max(0, margin_v)

                print(f"   📐 {vw}x{vh} | logo_y={logo_y_val} logo_h={logo_h_val} font={font_size} MarginV={margin_v}")

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

            # ---- WATERMARK TEXT (hiển thị ngay dưới vietsub) ----
            if req.watermark_lines:
                wm_texts = DEFAULT_WATERMARK_TEXT

                vw = video_width  or 720
                vh = video_height or 1280

                BELOW_LOGO_PADDING = 16
                wm_y = req.logo_y + req.logo_h + BELOW_LOGO_PADDING

                if vh <= 1300:
                    wm_x         = 65
                    wm_font_size = 30
                    wm_line_spacing = 50
                else:
                    wm_x         = 120
                    wm_font_size = 42
                    wm_line_spacing = 65

                print(f"   💧 Watermark Lines: {len(wm_texts)} dòng | pos=({wm_x},{wm_y}) font={wm_font_size}")
                print(f"      └─ Dưới logo: logo_y={req.logo_y} + logo_h={req.logo_h} + padding={BELOW_LOGO_PADDING}px")

                for i, text in enumerate(wm_texts):
                    escaped_text = text.replace(':', '\\:').replace("'", "\\'")
                    current_y = wm_y + (i * wm_line_spacing)

                    # Layer 1: Shadow
                    video_chain += (
                        f",drawtext=text='{escaped_text}':fontsize={wm_font_size}"
                        f":fontcolor=black@0.8:x={wm_x + 3}:y={current_y + 3}:borderw=0"
                    )
                    # Layer 2: Border đen
                    video_chain += (
                        f",drawtext=text='{escaped_text}':fontsize={wm_font_size}"
                        f":fontcolor=black:x={wm_x}:y={current_y}:borderw=4:bordercolor=black"
                    )
                    # Layer 3: Text vàng + viền cam
                    video_chain += (
                        f",drawtext=text='{escaped_text}':fontsize={wm_font_size}"
                        f":fontcolor=yellow:x={wm_x}:y={current_y}:borderw=2:bordercolor=orange"
                    )

                    print(f"      Dòng {i+1}: '{text}' → y={current_y}")

            # ---- Watermark text bounce ----
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

                escaped_text = req.branding_text.replace(':', '\\:').replace("'", "\\'")
                margin = 10
                range_x = f"w-tw-{margin*2}"
                move_x  = f"abs(mod({start_x}+{speed_x}*{direction_x}*t\\,2*({range_x}))-({range_x}))+{margin}"
                range_y = f"h-th-{margin*2}"
                move_y  = f"abs(mod({start_y}+{speed_y}*{direction_y}*t\\,2*({range_y}))-({range_y}))+{margin}"

                video_chain += (
                    f",drawtext=text='{escaped_text}':fontsize={font_size_wm}"
                    f":fontcolor=white@{alpha}:x='{move_x}':y='{move_y}'"
                    f":shadowcolor=black@0.3:shadowx=2:shadowy=2"
                )

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

                filters.append(f"[{brand_idx}:v]scale=225:150[v_brand]")
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

        # ============================================================
        # PHẦN 2: XỬ LÝ AUDIO - CHỐNG BẢN QUYỀN NÂNG CAO
        # ============================================================
        print("\n   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("   🛡️  CHỐNG BẢN QUYỀN - AUDIO TRANSFORMATION")
        print("   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if has_music:
            print(f"   🎚️  Chế độ: MIXING (Giọng + Nhạc nền)")
            duck, atk, rel = req.ducking_ratio or 5.0, req.attack_time or 50, req.release_time or 300
            voice_idx = 2
            music_idx = 1

            # Voice: giữ nguyên chất lượng AI voice, chỉ boost volume + bass
            voice_final = (
                f"[{voice_idx}:a]"
                f"volume={req.voice_volume or 3.0},"
                f"lowshelf=g=5:f=100:w=0.5[voice]"
            )
            filters.append(voice_final)
            filters.append(f"[voice]asplit[v_trig][v_mix]")

            # Music: transform để bypass Content ID
            music_filter, music_params = build_music_copyright_bypass(
                f"[{music_idx}:a]", m_vol
            )
            music_pitch    = music_params["music_pitch"]
            music_highpass = music_params["music_highpass"]
            music_lowpass  = music_params["music_lowpass"]
            music_tempo    = music_params["music_tempo"]

            filters.append(music_filter)
            filters.append(f"[bg][v_trig]sidechaincompress=threshold=0.1:ratio={duck}:attack={atk}:release={rel}[bg_duck]")
            filters.append(f"[bg_duck][v_mix]amix=inputs=2:duration=longest[a_out]")

        else:
            print(f"   🎚️  Chế độ: VOICE ONLY")
            voice_idx = 1
            music_pitch = None
            music_highpass = None
            music_lowpass = None
            music_tempo = None

            # Voice only: giữ nguyên, chỉ boost volume + bass
            voice_final = (
                f"[{voice_idx}:a]"
                f"volume={req.voice_volume or 3.0},"
                f"lowshelf=g=5:f=100:w=0.5[a_out]"
            )
            filters.append(voice_final)

        filter_complex = ";".join(filters)

        # ============================================================
        # ENCODE - dùng metadata khác để bypass fingerprint
        # ============================================================
        crf_value = random.choice([22, 23, 24])
        preset_choice = random.choice(["medium", "slow"])

        cmd = ["ffmpeg", "-y", "-progress", "pipe:1"] + inputs + [
            "-filter_complex", filter_complex,
            "-map", video_map, "-map", "[a_out]",
            "-c:v", "libx264", "-preset", preset_choice, "-crf", str(crf_value),
            "-metadata", f"comment=Processed_{get_timestamp_str()}",
            "-metadata", "encoder=CustomEncoder",
            "-c:a", "aac", "-b:a", "192k",
            out_file
        ]

        print(f"\n   ⚙️  ENCODE PARAMS: CRF={crf_value} | Preset={preset_choice}")
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
                              f"Thời gian: {elapsed:5.1f}s | ETA: ~{eta:5.1f}s")
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

        # Cleanup temp files
        for tmp in [extracted_audio_temp, ass_temp_path]:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                    print(f"   🗑️  Đã xóa file tạm: {tmp}")
                except Exception as e:
                    print(f"   ⚠️  Không xóa được file tạm: {e}")

        Logger.success("XỬ LÝ THÀNH CÔNG!", total_time)
        print(f"   ⏱️  Thời gian render: {render_time:.2f}s")
        print(f"   ⏱️  Tổng thời gian: {total_time:.2f}s")
        print(f"   📦 Kích thước file: {os.path.getsize(out_file) / 1024 / 1024:.2f} MB")
        print(f"   👉 File đích: {out_file}")

        return {
            "status": "success",
            "output_file": out_file,
            "copyright_bypass": {
                "video_transform": {
                    "color_grading": {
                        "saturation": saturation,
                        "contrast": contrast,
                        "brightness": brightness,
                        "gamma": gamma
                    },
                    "crop_pct": video_transform_params.get("crop_pct"),
                    "noise_strength": video_transform_params.get("noise_strength"),
                    "sharpen": video_transform_params.get("sharpen"),
                    "hue_shift": video_transform_params.get("hue_shift"),
                },
                "audio_transform": {
                    "music_pitch": music_pitch,
                    "music_tempo": music_tempo,
                    "music_highpass": music_highpass,
                    "music_lowpass": music_lowpass,
                } if has_music else None,
                "encode": {
                    "crf": crf_value,
                    "preset": preset_choice,
                }
            },
            "render_time": f"{render_time:.2f}s",
            "total_time": f"{total_time:.2f}s",
            "file_size_mb": f"{os.path.getsize(out_file) / 1024 / 1024:.2f}"
        }

    except subprocess.CalledProcessError as e:
        err_msg = e.stderr if isinstance(e.stderr, str) else str(e)
        print("\n❌ LỖI FFMPEG:\n" + "\n".join(err_msg.splitlines()[-10:]))
        for tmp in [extracted_audio_temp, ass_temp_path]:
            if tmp and os.path.exists(tmp):
                try: os.remove(tmp)
                except: pass
        raise HTTPException(500, "Lỗi khi chạy FFmpeg")

    except Exception as e:
        Logger.error("Lỗi hệ thống", e)
        for tmp in [extracted_audio_temp, ass_temp_path]:
            if tmp and os.path.exists(tmp):
                try: os.remove(tmp)
                except: pass
        raise HTTPException(500, str(e))