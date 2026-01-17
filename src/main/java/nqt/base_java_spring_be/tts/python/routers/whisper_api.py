import os
import time
from fastapi import APIRouter, HTTPException
from schemas import WhisperRequest
from ai_core import AI_MODELS
from config import WHISPER_BACKEND, MAX_SEGMENTS_PER_FILE
from utils import Logger, get_timestamp_str, write_srt_faster, normalize_segment_time

router = APIRouter()

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

            elapsed = time.time() - start_w
            print(f"\n📊 THỐNG KÊ:")
            print(f"   • Ngôn ngữ: {info.language} (Độ tin cậy: {info.language_probability:.2%})")
            print(f"   • Tổng số câu: {len(segments_list)}")
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