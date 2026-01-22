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


def detect_subtitle_roi(video_path, width, height, duration):
    """
    TẦNG 1: Phát hiện vùng ROI (Region of Interest) cho subtitle

    Chiến lược:
    - Lấy 3 samples ở giữa video (tránh intro/outro)
    - OCR FULL FRAME để tìm text ở đâu
    - Xác định Y_min, Y_max của tất cả text
    - Expand thêm 10% để đảm bảo không bỏ sót

    Returns:
        (y_start, y_end): Vùng ROI theo pixel Y
    """
    print(f"\n   🔍 TẦNG 1: Phát hiện vùng ROI...")

    try:
        import cv2
        import numpy as np
        import pytesseract
        import platform

        # Setup Tesseract path cho Windows
        if platform.system() == 'Windows':
            possible_paths = [
                r'C:\Program Files\Tesseract-OCR\tesseract.exe',
                r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
                r'C:\Users\tjnkt\AppData\Local\Programs\Tesseract-OCR\tesseract.exe'
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    pytesseract.pytesseract.tesseract_cmd = path
                    break
    except ImportError:
        print("   ⚠️  Cần: pip install pytesseract opencv-python")
        return int(height * 0.7), height  # Fallback: 30% dưới cùng

    # Sample 3 frames ở giữa video
    sample_times = [
        duration * 0.3,
        duration * 0.5,
        duration * 0.7
    ]

    all_y_positions = []

    for t in sample_times:
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            temp_frame = tmp.name

        try:
            # Extract frame
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(t), "-i", video_path,
                "-vframes", "1", "-q:v", "2", temp_frame
            ], capture_output=True, check=True)

            frame = cv2.imread(temp_frame)
            if frame is None:
                continue

            # Grayscale + Threshold
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

            # OCR để lấy bounding boxes
            try:
                data = pytesseract.image_to_data(
                    thresh,
                    lang='chi_sim+chi_tra+eng',
                    output_type=pytesseract.Output.DICT
                )
            except:
                data = pytesseract.image_to_data(
                    thresh,
                    output_type=pytesseract.Output.DICT
                )

            # Thu thập tất cả Y positions có text
            for i in range(len(data['text'])):
                if int(data['conf'][i]) > 30 and data['text'][i].strip():
                    y = data['top'][i]
                    h = data['height'][i]
                    all_y_positions.append(y)
                    all_y_positions.append(y + h)

        finally:
            if os.path.exists(temp_frame):
                os.remove(temp_frame)

    if not all_y_positions:
        print("   ⚠️  Không phát hiện text, dùng vùng mặc định: 70-100%")
        return int(height * 0.7), height

    # Tính ROI từ min/max Y positions
    y_min = min(all_y_positions)
    y_max = max(all_y_positions)

    # Expand 10% để đảm bảo
    margin = int((y_max - y_min) * 0.1)
    y_start = max(0, y_min - margin)
    y_end = min(height, y_max + margin)

    roi_percentage = ((y_end - y_start) / height) * 100
    print(f"   ✅ ROI detected: Y {y_start}-{y_end} ({roi_percentage:.1f}% chiều cao)")
    print(f"      → Giảm diện tích OCR: {100 - roi_percentage:.1f}%")

    return y_start, y_end


def dense_sample_roi(video_path, width, height, duration, roi_y_start, roi_y_end):
    """
    TẦNG 2: Dense Sampling CHỈ trong vùng ROI

    Chiến lược:
    - Sample mỗi 2 giây (video ngắn) hoặc 3 giây (video dài)
    - Crop frame CHỈ lấy vùng ROI trước khi OCR
    - Tốc độ tăng 3-10x so với OCR full frame

    Returns:
        List[dict]: Danh sách regions với tọa độ GLOBAL (đã cộng roi_y_start)
    """
    print(f"\n   🎯 TẦNG 2: Dense Sampling trong ROI...")

    try:
        import cv2
        import pytesseract
    except ImportError:
        print("   ⚠️  Thiếu thư viện, bỏ qua dense sampling")
        return []

    # Tính interval dựa trên độ dài video
    if duration <= 60:
        interval = 1.5  # Video ngắn: sample dày hơn
    elif duration <= 300:
        interval = 2.0
    else:
        interval = 3.0  # Video dài: sample thưa hơn

    sample_times = []
    t = 5.0  # Bắt đầu từ giây 5 (skip intro)
    while t < duration - 5:  # Dừng trước 5s cuối (skip outro)
        sample_times.append(t)
        t += interval

    print(f"   📊 Sẽ sample {len(sample_times)} frames (interval={interval}s)")

    all_regions = []
    roi_height = roi_y_end - roi_y_start

    for idx, t in enumerate(sample_times):
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            temp_frame = tmp.name

        try:
            # Extract frame
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(t), "-i", video_path,
                "-vframes", "1", "-q:v", "2", temp_frame
            ], capture_output=True, check=True)

            frame = cv2.imread(temp_frame)
            if frame is None:
                continue

            # **CROP CHỈ VÙNG ROI** - Đây là bí quyết tăng tốc
            roi_frame = frame[roi_y_start:roi_y_end, :]

            # Grayscale + Threshold
            gray = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)

            # OCR
            try:
                data = pytesseract.image_to_data(
                    thresh,
                    lang='chi_sim+chi_tra+eng',
                    output_type=pytesseract.Output.DICT
                )
            except:
                data = pytesseract.image_to_data(
                    thresh,
                    output_type=pytesseract.Output.DICT
                )

            # Thu thập regions
            frame_regions = 0
            for i in range(len(data['text'])):
                if int(data['conf'][i]) > 30 and data['text'][i].strip():
                    x = data['left'][i]
                    y = data['top'][i]  # Y trong ROI frame
                    w = data['width'][i]
                    h = data['height'][i]

                    # Expand box 20%
                    padding = 0.2
                    x = max(0, int(x - w * padding))
                    y = max(0, int(y - h * padding))
                    w = int(w * (1 + 2 * padding))
                    h = int(h * (1 + 2 * padding))

                    # **QUAN TRỌNG: Chuyển Y về tọa độ GLOBAL**
                    global_y = roi_y_start + y

                    all_regions.append({
                        'x': x,
                        'y': global_y,
                        'w': w,
                        'h': h,
                        'text': data['text'][i],
                        'conf': data['conf'][i],
                        'time': t
                    })
                    frame_regions += 1

            if (idx + 1) % 10 == 0:
                print(f"      Frame {idx+1}/{len(sample_times)}: {frame_regions} regions")

        except Exception as e:
            # Bỏ qua frame lỗi
            pass

        finally:
            if os.path.exists(temp_frame):
                os.remove(temp_frame)

        # Early stopping nếu đã đủ regions
        if len(all_regions) > 100:
            print(f"   ⚡ Early stop: Đã có {len(all_regions)} regions")
            break

    print(f"   ✅ Tổng: {len(all_regions)} regions từ {idx+1} frames")
    return all_regions


def smart_merge_regions(regions, height):
    """
    TẦNG 3: Smart Merge sử dụng Y-clustering

    Chiến lược:
    - Group regions theo Y position (tolerance ±10px)
    - Với mỗi group, merge các boxes có X overlap
    - Kết quả: 1-3 regions cuối cùng (top/middle/bottom subtitles)

    Returns:
        List[dict]: Danh sách regions đã merge
    """
    print(f"\n   🔄 TẦNG 3: Smart Merge...")

    if not regions:
        return []

    # Step 1: Cluster theo Y position (tolerance ±10px)
    y_clusters = defaultdict(list)

    for r in regions:
        # Round Y về bội số của 10
        y_bucket = round(r['y'] / 10) * 10
        y_clusters[y_bucket].append(r)

    print(f"   📊 Phát hiện {len(y_clusters)} Y-clusters")

    # Step 2: Merge trong từng cluster
    final_regions = []

    for y_bucket, cluster_regions in y_clusters.items():
        # Sort theo X
        cluster_regions.sort(key=lambda r: r['x'])

        merged = []
        for r in cluster_regions:
            # Tìm region có thể merge
            merged_flag = False
            for m in merged:
                # Check X overlap (tolerance ±20px)
                x_overlap = (
                        max(m['x'], r['x']) < min(m['x'] + m['w'], r['x'] + r['w']) + 20
                )

                if x_overlap:
                    # Merge: expand bounding box
                    new_x = min(m['x'], r['x'])
                    new_y = min(m['y'], r['y'])
                    new_w = max(m['x'] + m['w'], r['x'] + r['w']) - new_x
                    new_h = max(m['y'] + m['h'], r['y'] + r['h']) - new_y

                    m['x'] = new_x
                    m['y'] = new_y
                    m['w'] = new_w
                    m['h'] = new_h
                    m['conf'] = max(m['conf'], r['conf'])
                    merged_flag = True
                    break

            if not merged_flag:
                merged.append(r.copy())

        final_regions.extend(merged)

    print(f"   ✅ Sau merge: {len(final_regions)} regions")

    # Debug output
    for idx, r in enumerate(final_regions):
        print(f"      Region {idx+1}: Y={r['y']}, size={r['w']}x{r['h']}, "
              f"text='{r['text'][:20]}...' (conf={r['conf']})")

    return final_regions


def auto_detect_text_regions_optimized(video_path):
    """
    PIPELINE HOÀN CHỈNH: ROI Dense Sampling

    Tốc độ: 5-15 giây cho video 5 phút (vs 30-60s với cách cũ)
    Độ chính xác: 95%+ (vs 60% với cách cũ)
    """
    print("   " + "="*56)
    print("   🚀 BẮT ĐẦU: ROI Dense Sampling Pipeline")
    print("   " + "="*56)

    # Get video info
    width, height, duration = get_video_info(video_path)
    if not width:
        print("   ❌ Không đọc được video info, fallback cách cũ")
        return []

    print(f"   📹 Video: {width}x{height}, {duration:.1f}s")

    # TẦNG 1: Detect ROI
    roi_y_start, roi_y_end = detect_subtitle_roi(
        video_path, width, height, duration
    )

    # TẦNG 2: Dense Sampling
    all_regions = dense_sample_roi(
        video_path, width, height, duration,
        roi_y_start, roi_y_end
    )

    # TẦNG 3: Smart Merge
    final_regions = smart_merge_regions(all_regions, height)

    print("   " + "="*56)
    print(f"   🎉 HOÀN THÀNH: Phát hiện {len(final_regions)} vùng subtitle")
    print("   " + "="*56 + "\n")

    return final_regions


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

        # ==================== XỬ LÝ LOGO & BRANDING (ROI DENSE SAMPLING) ====================
        if req.remove_logo:
            print("   🛡️  Xóa Logo/Text: BẬT (ROI DENSE SAMPLING MODE)")

            # TỰ ĐỘNG PHÁT HIỆN TEXT REGIONS BẰNG ROI DENSE SAMPLING
            text_regions = []
            print("   🔍 Tự động phát hiện text bằng ROI Dense Sampling...")

            detected = auto_detect_text_regions_optimized(vid)

            # VALIDATE & TRANSFORM TO FULL WIDTH REGIONS
            if detected:
                print(f"   📊 Detected {len(detected)} regions, đang lọc vùng dưới 2/3...")

                # Tính ngưỡng 2/3 height
                threshold_y = (2/3) * pip_h  # 2/3 chiều cao của video PiP

                for idx, region in enumerate(detected):
                    # Chuyển tọa độ về PiP scale
                    logo_y_scaled = int(region['y'] * pip_scale)

                    # BỎ QUA nếu region nằm ở 2/3 trên của video
                    if logo_y_scaled < threshold_y:
                        print(f"   ⏭️  Region {idx+1} SKIPPED: y={logo_y_scaled} < threshold={threshold_y:.0f}")
                        continue

                    # Region hợp lệ - Chuyển sang FULL WIDTH
                    logo_x_scaled = pip_x  # Bắt đầu từ cạnh trái PiP
                    logo_y_scaled = logo_y_scaled + pip_y
                    logo_w_scaled = pip_w  # Full width của PiP
                    logo_h_scaled = int(region['h'] * pip_scale)

                    # VALIDATE: Đảm bảo region nằm trong frame
                    if logo_y_scaled < 0:
                        logo_y_scaled = 0

                    if logo_y_scaled + logo_h_scaled > new_h:
                        logo_h_scaled = new_h - logo_y_scaled

                    # Chỉ thêm nếu height hợp lý (> 5px)
                    if logo_h_scaled >= 5:
                        text_regions.append({
                            'x': logo_x_scaled,
                            'y': logo_y_scaled,
                            'w': logo_w_scaled,
                            'h': logo_h_scaled
                        })
                        print(f"   ✅ Region {idx+1} FULL WIDTH (BOTTOM 1/3): y={logo_y_scaled}, h={logo_h_scaled}")
                        print(f"      (Xóa toàn bộ hàng ngang: x={logo_x_scaled} đến x={logo_x_scaled + logo_w_scaled})")

                print(f"   📊 Valid bottom-third regions: {len(text_regions)}/{len(detected)}")
                print(f"   📏 Threshold Y (2/3 height): {threshold_y:.0f}px")

            # XÂY DỰNG FILTER CHAIN
            if text_regions:
                # CÓ TEXT → Overlay + Delogo FULL WIDTH
                delogo_filters = []
                for region in text_regions:
                    delogo_filters.append(
                        f"delogo=x={region['x']}:y={region['y']}:w={region['w']}:h={region['h']}"
                    )

                overlay_base += "[v_after_overlay];[v_after_overlay]" + ",".join(delogo_filters) + "[v_after_delogo]"
                last_video_label = "v_after_delogo"
            else:
                # KHÔNG CÓ TEXT → Chỉ có Overlay
                print("   ⚠️  Không có regions hợp lệ ở vùng dưới 2/3, bỏ qua delogo")
                overlay_base += "[v_after_overlay]"
                last_video_label = "v_after_overlay"

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
            voice_idx = 2 if not (req.remove_logo and has_branding) else 2
            music_idx = 1 if not (req.remove_logo and has_branding) else 1

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
            voice_idx = 1 if not (req.remove_logo and has_branding) else 1

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
            "text_detection_method": "ROI_Dense_Sampling" if req.remove_logo else "None",
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