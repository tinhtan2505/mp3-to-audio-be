import os
import re
import subprocess
import tempfile
from pathlib import Path
from collections import defaultdict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter()

# ==================== PYDANTIC MODELS ====================

class DetectTextRequest(BaseModel):
    video_path: str
    skip_top_two_thirds: bool = True  # Mặc định bỏ qua 2/3 trên

class TextRegion(BaseModel):
    logo_x: int
    logo_y: int
    logo_w: int
    logo_h: int
    confidence: float
    sample_text: str

class DetectTextResponse(BaseModel):
    status: str
    video_width: int
    video_height: int
    video_duration: float
    roi_detected: dict
    total_regions_found: int
    regions_after_filter: int
    skip_threshold_y: Optional[int]
    regions: List[TextRegion]

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


# ==================== API ENDPOINT ====================

@router.post("/api/v1/dubbing/detect-text-regions", response_model=DetectTextResponse)
def api_detect_text_regions(req: DetectTextRequest):
    """
    API phát hiện vùng text/logo trong video sử dụng ROI Dense Sampling

    Returns:
        - Danh sách regions với tọa độ CHƯA scaled (original video size)
        - Mặc định BỎ QUA regions ở 2/3 trên của video
    """
    import time

    start_time = time.time()
    print("   " + "="*56)
    print("   🚀 API PHÁT HIỆN VÙNG TEXT/LOGO")
    print("   " + "="*56)

    try:
        # Kiểm tra file tồn tại
        if not os.path.exists(req.video_path):
            raise FileNotFoundError(f"Video không tồn tại: {req.video_path}")

        # Lấy thông tin video
        width, height, duration = get_video_info(req.video_path)
        if not width:
            raise HTTPException(500, "Không thể đọc thông tin video")

        print(f"   📹 Video: {width}x{height}, {duration:.1f}s")

        # TẦNG 1: Detect ROI
        roi_y_start, roi_y_end = detect_subtitle_roi(
            req.video_path, width, height, duration
        )

        # TẦNG 2: Dense Sampling
        all_regions = dense_sample_roi(
            req.video_path, width, height, duration,
            roi_y_start, roi_y_end
        )

        # TẦNG 3: Smart Merge
        merged_regions = smart_merge_regions(all_regions, height)

        # FILTER: Bỏ qua 2/3 trên nếu được yêu cầu
        filtered_regions = []
        skip_threshold_y = None

        if req.skip_top_two_thirds:
            skip_threshold_y = int((2/3) * height)
            print(f"\n   📏 Ngưỡng lọc (2/3 chiều cao): {skip_threshold_y}px")

            for idx, region in enumerate(merged_regions):
                # Kiểm tra Y position (CHƯA scaled)
                if region['y'] < skip_threshold_y:
                    continue

                filtered_regions.append(region)
                print(f"   ✅ Region {idx+1} KEPT (bottom 1/3): y={region['y']}, size={region['w']}x{region['h']}")
        else:
            filtered_regions = merged_regions
            print("\n   ℹ️  Không lọc regions (giữ tất cả)")

        # Chuyển đổi sang format output
        output_regions = []
        for region in filtered_regions:
            output_regions.append(TextRegion(
                logo_x=region['x'],
                logo_y=region['y'],
                logo_w=region['w'],
                logo_h=region['h'],
                confidence=region.get('conf', 0),
                sample_text=region.get('text', '')[:50]  # Giới hạn 50 ký tự
            ))

        total_time = time.time() - start_time

        print("   " + "="*56)
        print(f"   🎉 HOÀN THÀNH: Phát hiện {len(output_regions)} vùng text/logo")
        print(f"   ⏱️  Thời gian: {total_time:.2f}s")
        print("   " + "="*56 + "\n")

        return DetectTextResponse(
            status="success",
            video_width=width,
            video_height=height,
            video_duration=duration,
            roi_detected={
                "y_start": roi_y_start,
                "y_end": roi_y_end,
                "height_percentage": f"{((roi_y_end - roi_y_start) / height) * 100:.1f}%"
            },
            total_regions_found=len(merged_regions),
            regions_after_filter=len(filtered_regions),
            skip_threshold_y=skip_threshold_y,
            regions=output_regions
        )

    except FileNotFoundError as e:
        print(f"   ❌ Lỗi: {e}")
        raise HTTPException(404, str(e))
    except Exception as e:
        print(f"   ❌ Lỗi: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(500, str(e))