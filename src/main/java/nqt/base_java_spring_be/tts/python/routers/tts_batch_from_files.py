import os
import time
import json
import librosa
import soundfile as sf
import numpy as np
import pysrt
import glob
import sys
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import SAMPLE_RATE
from utils import Logger, get_timestamp_str

router = APIRouter()

class TtsBatchFromFilesRequest(BaseModel):
    input_srt_path: str
    audio_files_dir: str = None  # Nếu None, sẽ dùng thư mục tts ngang hàng với SRT
    metadata_file: str = None    # Nếu None, sẽ tìm timing_metadata.json trong audio_files_dir
    batch_size: int = 50        # Chỉ dùng để quản lý vòng lặp nội bộ

@router.post("/api/v1/dubbing/tts-batch-from-files")
async def api_tts_batch_from_files_v2(req: TtsBatchFromFilesRequest):
    """
    Ghép các file MP3 đã xử lý timing vào audio tổng dựa trên Metadata.
    Đã lược bỏ lưu checkpoint và cập nhật UI tiến độ trên 1 dòng.
    """
    start_time = time.time()
    try:
        path = os.path.abspath(req.input_srt_path)

        # Kiểm tra file SRT
        if not os.path.exists(path):
            search_dir = os.path.dirname(path) if not os.path.isdir(path) else path
            srt_candidates = glob.glob(os.path.join(search_dir, "*vi_FULL*.srt"))
            if not srt_candidates:
                srt_candidates = glob.glob(os.path.join(search_dir, "*.srt"))

            if not srt_candidates:
                raise ValueError(f"File SRT không tồn tại tại: {search_dir}")

            path = os.path.abspath(srt_candidates[0])
            print(f"\n⚠️  Sử dụng file SRT thay thế: {os.path.basename(path)}")

        subs = pysrt.open(path)
        if not subs:
            raise ValueError("File SRT rỗng")

        total_subs = len(subs)

        # Xác định thư mục audio
        audio_dir = os.path.abspath(req.audio_files_dir) if req.audio_files_dir else os.path.join(os.path.dirname(path), "tts")
        if not os.path.exists(audio_dir):
            raise ValueError(f"Thư mục audio không tồn tại: {audio_dir}")

        # Xác định file metadata
        metadata_path = os.path.abspath(req.metadata_file) if req.metadata_file else os.path.join(audio_dir, "timing_metadata.json")
        if not os.path.exists(metadata_path):
            raise ValueError(f"Thiếu file metadata: {metadata_path}")

        # Load metadata
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)

        # Khởi tạo buffer audio tổng (dự phòng thêm 20s)
        total_seconds = (subs[-1].end.ordinal / 1000.0) + 20.0
        final_audio = np.zeros(int(total_seconds * SAMPLE_RATE), dtype=np.float32)

        Logger.section("TTS BATCH PROCESSING (V2)")
        print(f" • SRT: {os.path.basename(path)}")
        print(f" • Audio Dir: {audio_dir}")
        print(f" • Metadata: {os.path.basename(metadata_path)}\n")

        processed_count = 0
        missing_files = []
        missing_metadata = []

        # Xử lý ghép file
        for i, sub in enumerate(subs):
            srt_index = sub.index
            meta_key = str(srt_index)

            # Kiểm tra metadata và file
            if meta_key not in metadata:
                missing_metadata.append(srt_index)
                continue

            mp3_file = os.path.join(audio_dir, f"{srt_index}.mp3")
            if not os.path.exists(mp3_file):
                missing_files.append(srt_index)
                continue

            try:
                # Load và xử lý audio
                y, _ = librosa.load(mp3_file, sr=SAMPLE_RATE)
                y_trimmed, _ = librosa.effects.trim(y, top_db=30)

                meta = metadata[meta_key]
                start_sample = int(meta['start_sec'] * SAMPLE_RATE)
                end_sample = start_sample + len(y_trimmed)

                # Mở rộng buffer nếu cần
                if end_sample > len(final_audio):
                    padding = np.zeros(SAMPLE_RATE * 30, dtype=np.float32) # Thêm 30s
                    final_audio = np.concatenate((final_audio, padding))

                # Mix audio
                final_audio[start_sample:end_sample] += y_trimmed
                processed_count += 1

                # CẬP NHẬT TIẾN ĐỘ TRÊN 1 DÒNG
                pct = (i + 1) / total_subs * 100
                bar = "#" * int(pct / 5) + "." * (20 - int(pct / 5))
                sys.stdout.write(f"\r🚀 Tiến độ: [{bar}] {pct:.1f}% | Câu: {i+1}/{total_subs} | Đã xử lý: {processed_count} | Lỗi: {len(missing_files) + len(missing_metadata)}")
                sys.stdout.flush()

            except Exception as e:
                # Nếu lỗi trong lúc load file cụ thể, in ra dòng mới để không đè lên thanh progress
                print(f"\n❌ Lỗi tại câu {srt_index}: {str(e)[:50]}")
                continue

        # Lưu file cuối cùng
        print("\n\n💾 Đang kết xuất file audio cuối cùng...")
        final_valid_len = int(subs[-1].end.ordinal/1000 * SAMPLE_RATE) + int(0.5 * SAMPLE_RATE)
        final_audio = final_audio[:final_valid_len]

        srt_dir = os.path.dirname(path)
        out_name = os.path.join(srt_dir, "vocals_vi_audio.wav")
        sf.write(out_name, final_audio, SAMPLE_RATE)

        elapsed = time.time() - start_time

        # Thống kê tổng kết
        Logger.success(f"TTS hoàn tất: {os.path.basename(out_name)}", elapsed)
        print(f"\n📊 THỐNG KÊ:")
        print(f" • Tổng câu: {total_subs:,}")
        print(f" • Thành công: {processed_count:,}")
        print(f" • Thiếu File/Metadata: {len(missing_files)}/{len(missing_metadata)}")
        print(f" • Thời gian: {elapsed:.1f}s ({elapsed/60:.1f} phút)")

        return {
            "status": "success" if processed_count == total_subs else "partial_success",
            "output_file": out_name,
            "processed": processed_count,
            "total": total_subs,
            "missing_files": len(missing_files),
            "missing_metadata": len(missing_metadata),
            "elapsed_seconds": elapsed,
            "success_rate": round(processed_count / total_subs * 100, 2)
        }

    except Exception as e:
        Logger.error("Lỗi hệ thống TTS Batch", e)
        raise HTTPException(500, str(e))