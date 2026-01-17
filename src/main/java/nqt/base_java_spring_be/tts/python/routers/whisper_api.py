import os
import time
from fastapi import APIRouter, HTTPException
from schemas import WhisperRequest
from ai_core import AI_MODELS
from config import WHISPER_BACKEND, MAX_SEGMENTS_PER_FILE
from utils import Logger, get_timestamp_str, write_srt_faster, normalize_segment_time

router = APIRouter()

def filter_repeated_segments(segments_list, max_repetition=3):
    """
    Lọc bỏ các segment lặp lại liên tiếp
    max_repetition: Số lần cho phép lặp text giống nhau
    """
    if not segments_list:
        return []

    filtered = []
    prev_texts = []
    repetition_count = 0

    for seg in segments_list:
        # Xử lý cả dict và object
        if isinstance(seg, dict):
            text = seg.get('text', '').strip()
        else:
            text = getattr(seg, 'text', '').strip()

        # Bỏ qua segment rỗng hoặc quá ngắn
        if not text or len(text) < 2:
            continue

        # Kiểm tra lặp lại
        if text in prev_texts[-max_repetition:]:
            repetition_count += 1
            # Nếu lặp quá nhiều, bỏ qua
            if repetition_count >= max_repetition:
                continue
        else:
            repetition_count = 0

        filtered.append(seg)
        prev_texts.append(text)

        # Giữ lịch sử 10 câu gần nhất
        if len(prev_texts) > 10:
            prev_texts.pop(0)

    return filtered


def detect_hallucination(segments_list):
    """
    Phát hiện hallucination bằng cách kiểm tra:
    - Câu lặp quá nhiều lần
    - Câu quá ngắn (1-2 ký tự)
    - Tỷ lệ câu giống nhau cao
    """
    if len(segments_list) < 10:
        return False

    text_counts = {}
    short_count = 0

    for seg in segments_list:
        # Xử lý cả dict và object
        if isinstance(seg, dict):
            text = seg.get('text', '').strip()
        else:
            text = getattr(seg, 'text', '').strip()

        text_counts[text] = text_counts.get(text, 0) + 1
        if len(text) <= 2:
            short_count += 1

    # Nếu >30% câu quá ngắn => hallucination
    if short_count / len(segments_list) > 0.3:
        return True

    # Nếu 1 câu lặp >20% tổng số => hallucination
    max_repeat = max(text_counts.values())
    if max_repeat / len(segments_list) > 0.2:
        return True

    return False


@router.post("/api/v1/dubbing/whisper")
def api_whisper(req: WhisperRequest):
    if not AI_MODELS["whisper"]:
        raise HTTPException(500, "Model Whisper chưa được tải")

    try:
        path = os.path.abspath(req.input_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"File không tồn tại: {path}")

        Logger.section("WHISPER - TÁCH LỜI THOẠI")
        print(f"   • Đầu vào: {os.path.basename(path)}")
        start_w = time.time()

        if WHISPER_BACKEND == "faster":
            print("   ⏳ Đang xử lý (Chế độ chính xác thời gian)...")
            segments, info = AI_MODELS["whisper"].transcribe(
                path,
                language="zh",
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=400),
                condition_on_previous_text=False,
                beam_size=1, best_of=1, temperature=0.0,
                repetition_penalty=1.2, no_speech_threshold=0.6,
                word_timestamps=True,
                compression_ratio_threshold=2.0, log_prob_threshold=-1.0,
                initial_prompt=None
            )

            # Chuẩn hóa thời gian
            segments_list = [normalize_segment_time(seg) for seg in segments]

            original_count = len(segments_list)
            segments_list = filter_repeated_segments(segments_list, max_repetition=2)
            filtered_count = original_count - len(segments_list)
            if detect_hallucination(segments_list):
                print("\n⚠️  CẢNH BÁO: Phát hiện hallucination!")
                print("   Khuyến nghị: Kiểm tra lại file audio hoặc giảm độ dài")

            elapsed = time.time() - start_w
            print(f"\n📊 THỐNG KÊ:")
            print(f"   • Ngôn ngữ: {info.language} (Độ tin cậy: {info.language_probability:.2%})")
            print(f"   • Tổng số câu: {len(segments_list)} (Đã lọc: {filtered_count})")
            print(f"   • Thời gian: {elapsed:.2f}s")

            # Lưu file SRT (Chia nhỏ nếu cần)
            out_dir = os.path.dirname(path)
            base_filename = os.path.splitext(os.path.basename(path))[0].split('_')[0]
            timestamp_str = get_timestamp_str()
            output_files_list = []

            chunks = [segments_list[i:i + MAX_SEGMENTS_PER_FILE]
                      for i in range(0, len(segments_list), MAX_SEGMENTS_PER_FILE)]

            print(f"   ✂️  Chia thành {len(chunks)} phần (Tối đa {MAX_SEGMENTS_PER_FILE} câu/file)...")
            current_srt_index = 1

            for idx, chunk in enumerate(chunks):
                part_suffix = f"_part{idx+1:02d}"
                out_name = f"{base_filename}_cn_{timestamp_str}{part_suffix}.srt"
                full_path = os.path.join(out_dir, out_name)
                write_srt_faster(chunk, full_path, start_index=current_srt_index)
                output_files_list.append(full_path)
                print(f"      -> Đã ghi: {out_name} => 📂 {full_path}")
                current_srt_index += len(chunk)

            Logger.success(f"Whisper hoàn tất. Tổng {len(chunks)} files.", elapsed)
            return {
                "status": "success", "engine": "faster-whisper",
                "total_segments": len(segments_list), "split_count": len(chunks),
                "output_files": output_files_list
            }
        else:
            raise HTTPException(400, "Chế độ này chỉ hỗ trợ faster-whisper")
    except Exception as e:
        Logger.error("Lỗi Whisper", e)
        raise HTTPException(500, str(e))