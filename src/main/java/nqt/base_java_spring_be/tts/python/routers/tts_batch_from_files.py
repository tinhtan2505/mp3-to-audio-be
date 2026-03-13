import os
import time
import json
import librosa
import soundfile as sf
import numpy as np
import pysrt
import glob
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import SAMPLE_RATE
from utils import Logger, get_timestamp_str

router = APIRouter()

class TtsBatchFromFilesRequest(BaseModel):
    input_srt_path: str
    audio_files_dir: str = None  # Nếu None, sẽ dùng thư mục tts ngang hàng với SRT
    metadata_file: str = None  # Nếu None, sẽ tìm timing_metadata.json trong audio_files_dir
    batch_size: int = 50  # Chỉ dùng để log progress


# --- BATCH TTS TỪ CÁC FILE MP3 CÓ SẴN (SỬ DỤNG METADATA) ---
@router.post("/api/v1/dubbing/tts-batch-from-files")
async def api_tts_batch_from_files_v2(req: TtsBatchFromFilesRequest):
    """
    Ghép các file MP3 đã xử lý timing vào audio tổng
    SỬ DỤNG METADATA - KHÔNG TÍNH TOÁN LẠI TIMING
    """
    start_time = time.time()
    try:
        path = os.path.abspath(req.input_srt_path)

        if not os.path.exists(path):
            # Tìm file SRT có chứa "vi_FULL" trong thư mục
            search_dir = os.path.dirname(path) if not os.path.isdir(path) else path
            srt_candidates = glob.glob(os.path.join(search_dir, "*vi_FULL*.srt"))

            if not srt_candidates:
                # Fallback: tìm bất kỳ file .srt nào
                srt_candidates = glob.glob(os.path.join(search_dir, "*.srt"))

            if not srt_candidates:
                raise ValueError(f"File SRT không tồn tại và không tìm thấy file SRT nào trong: {search_dir}")

            path = os.path.abspath(srt_candidates[0])
            print(f"   ⚠️  File SRT không tồn tại, dùng file tìm được: {os.path.basename(path)}")

        subs = pysrt.open(path)
        if not subs:
            raise ValueError("SRT rỗng")

        total_subs = len(subs)

        # Xác định thư mục chứa file audio
        if req.audio_files_dir:
            audio_dir = os.path.abspath(req.audio_files_dir)
        else:
            srt_dir = os.path.dirname(path)
            audio_dir = os.path.join(srt_dir, "tts")

        if not os.path.exists(audio_dir):
            raise ValueError(f"Thư mục audio không tồn tại: {audio_dir}")

        # Xác định file metadata
        if req.metadata_file:
            metadata_path = os.path.abspath(req.metadata_file)
        else:
            metadata_path = os.path.join(audio_dir, "timing_metadata.json")

        if not os.path.exists(metadata_path):
            raise ValueError(
                f"File metadata không tồn tại: {metadata_path}\n"
                f"Metadata được tạo tự động khi chạy /api/v1/dubbing/whisper\n"
                f"Nếu bạn chưa có metadata, hãy dùng endpoint cũ: /api/v1/dubbing/tts-batch-from-files"
            )

        # Load metadata
        print(f"\n📄 Đang load metadata: {os.path.basename(metadata_path)}")
        with open(metadata_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)

        print(f"   ✓ Đã load metadata cho {len(metadata)} câu")

        CHECKPOINT_INTERVAL = 500

        # Tính thời lượng tổng từ SRT
        total_seconds = (subs[-1].end.ordinal / 1000.0) + 20.0
        final_audio = np.zeros(int(total_seconds * SAMPLE_RATE), dtype=np.float32)

        Logger.section("TTS BATCH FROM FILES V2 - USING METADATA")
        print(f"   • File SRT: {os.path.basename(path)}")
        print(f"   • Thư mục audio: {audio_dir}")
        print(f"   • Metadata: {os.path.basename(metadata_path)}")
        print(f"   • Tổng câu: {total_subs:,}")
        print(f"   • Metadata entries: {len(metadata):,}\n")

        processed_count = 0
        missing_files = []
        missing_metadata = []
        last_checkpoint_sample = 0

        # Xử lý từng câu theo thứ tự
        BATCH_SIZE = req.batch_size
        for batch_start in range(0, total_subs, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_subs)
            batch_subs = subs[batch_start:batch_end]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (total_subs + BATCH_SIZE - 1) // BATCH_SIZE

            print(f"\n📦 Batch {batch_num}/{total_batches} | Câu {batch_start+1}-{batch_end} | Tiến độ: {(batch_end/total_subs*100):.1f}%")

            batch_start_time = time.time()

            for i, sub in enumerate(batch_subs):
                srt_index = sub.index  # Index từ SRT file (1-based)

                # Kiểm tra metadata tồn tại
                meta_key = str(srt_index)
                if meta_key not in metadata:
                    missing_metadata.append(srt_index)
                    print(f"   [⚠️  {srt_index:04d}] Không có metadata")
                    continue

                meta = metadata[meta_key]

                # Đường dẫn file MP3
                mp3_file = os.path.join(audio_dir, f"{srt_index}.mp3")

                if not os.path.exists(mp3_file):
                    missing_files.append(srt_index)
                    print(f"   [❌ {srt_index:04d}] File không tồn tại: {srt_index}.mp3")
                    continue

                try:
                    # Load audio (ĐÃ XỬ LÝ SẴN - không cần tăng tốc lại)
                    y, _ = librosa.load(mp3_file, sr=SAMPLE_RATE)
                    y_trimmed, _ = librosa.effects.trim(y, top_db=30)

                    # SỬ DỤNG TIMING TỪ METADATA (không tính lại)
                    start_sample = int(meta['start_sec'] * SAMPLE_RATE)
                    end_sample = start_sample + len(y_trimmed)

                    # Kiểm tra overflow
                    if end_sample > len(final_audio):
                        padding = np.zeros(end_sample - len(final_audio) + SAMPLE_RATE, dtype=np.float32)
                        final_audio = np.concatenate((final_audio, padding))

                    # Ghép audio
                    final_audio[start_sample:end_sample] += y_trimmed
                    processed_count += 1

                    # Log mỗi 10 câu
                    if (i + 1) % 10 == 0 or i == len(batch_subs) - 1:
                        duration = len(y_trimmed) / SAMPLE_RATE
                        status = meta.get('status', '✓')
                        progress_pct = (processed_count / total_subs * 100)

                        print(f"   [{srt_index:04d}] {status} | "
                              f"{meta['start_sec']:.2f}s-{meta['end_sec']:.2f}s | "
                              f"Duration: {duration:.2f}s | "
                              f"Progress: {processed_count}/{total_subs} ({progress_pct:.1f}%)")

                except Exception as e:
                    print(f"   [❌ {srt_index:04d}] Lỗi xử lý: {str(e)[:50]}")
                    continue

            batch_time = time.time() - batch_start_time
            print(f"   ⏱️  Batch time: {batch_time:.1f}s | Avg: {batch_time/len(batch_subs):.2f}s/câu")

            # Checkpoint
            if processed_count % CHECKPOINT_INTERVAL == 0 and processed_count > 0:
                checkpoint_file = f"{path.replace('.srt', '')}_checkpoint_{processed_count}.wav"
                end_sample = int(meta['end_sec'] * SAMPLE_RATE) + len(y_trimmed)
                checkpoint_audio = final_audio[:end_sample]
                sf.write(checkpoint_file, checkpoint_audio, SAMPLE_RATE)
                print(f"\n💾 Checkpoint saved: {os.path.basename(checkpoint_file)}")
                last_checkpoint_sample = end_sample

        # Lưu file cuối
        final_valid_len = int(subs[-1].end.ordinal/1000 * SAMPLE_RATE) + int(0.5 * SAMPLE_RATE)
        final_audio = final_audio[:final_valid_len]

        # Tên file cố định
        srt_dir = os.path.dirname(path)
        out_name = os.path.join(srt_dir, "vocals_vi_audio.wav")
        sf.write(out_name, final_audio, SAMPLE_RATE)

        # Xóa các file checkpoint đã tạo
        checkpoint_pattern = path.replace('.srt', '') + '_checkpoint_*.wav'
        checkpoint_files = glob.glob(checkpoint_pattern)
        if checkpoint_files:
            print(f"\n🗑️  Đang xóa {len(checkpoint_files)} file checkpoint...")
            for checkpoint_file in checkpoint_files:
                try:
                    os.remove(checkpoint_file)
                    print(f"   ✓ Đã xóa: {os.path.basename(checkpoint_file)}")
                except Exception as e:
                    print(f"   ⚠️  Không thể xóa {os.path.basename(checkpoint_file)}: {e}")

        elapsed = time.time() - start_time

        # Tổng kết
        Logger.success(f"TTS hoàn tất: {os.path.basename(out_name)}", elapsed)
        print(f"\n📊 THỐNG KÊ TỔNG KẾT:")
        print(f"   • Tổng câu trong SRT: {total_subs:,}")
        print(f"   • Câu xử lý thành công: {processed_count:,}")
        print(f"   • Câu thiếu metadata: {len(missing_metadata):,}")
        print(f"   • Câu thiếu file MP3: {len(missing_files):,}")
        print(f"   • Tỷ lệ thành công: {(processed_count/total_subs*100):.1f}%")
        print(f"   • Thời gian: {elapsed:.1f}s ({elapsed/60:.1f} phút)")
        print(f"   • Tốc độ: {processed_count/elapsed:.1f} câu/giây")

        # Thống kê từ metadata
        speedup_files = sum(1 for m in metadata.values() if m.get('speedup_percent', 0) > 0)
        if speedup_files > 0:
            avg_speedup = sum(m.get('speedup_percent', 0) for m in metadata.values()) / speedup_files
            print(f"\n⚡ THỐNG KÊ TĂNG TỐC (từ metadata):")
            print(f"   • Số file đã tăng tốc: {speedup_files:,}/{len(metadata):,}")
            print(f"   • % tăng tốc trung bình: {avg_speedup:.1f}%")

        if missing_metadata:
            print(f"\n⚠️ DANH SÁCH THIẾU METADATA ({len(missing_metadata)} câu):")
            for idx in sorted(missing_metadata)[:20]:
                print(f"   - Index {idx}")
            if len(missing_metadata) > 20:
                print(f"   ... và {len(missing_metadata) - 20} câu khác")

        if missing_files:
            print(f"\n⚠️ DANH SÁCH FILE THIẾU ({len(missing_files)} file):")
            for idx in sorted(missing_files)[:20]:
                print(f"   - Index {idx}: {idx}.mp3")
            if len(missing_files) > 20:
                print(f"   ... và {len(missing_files) - 20} file khác")
            print(f"\n💡 Hãy tạo các file MP3 còn thiếu trong thư mục: {audio_dir}")

        return {
            "status": "success" if processed_count == total_subs else "partial_success",
            "output_file": out_name,
            "processed": processed_count,
            "total": total_subs,
            "missing_files": len(missing_files),
            "missing_metadata": len(missing_metadata),
            "missing_file_indices": sorted(missing_files),
            "missing_metadata_indices": sorted(missing_metadata),
            "elapsed_seconds": elapsed,
            "success_rate": round(processed_count / total_subs * 100, 2),
            "audio_directory": audio_dir,
            "metadata_file": metadata_path,
            "speedup_count": speedup_files
        }

    except Exception as e:
        Logger.error("Lỗi TTS Batch From Files V2", e)
        raise HTTPException(500, str(e))