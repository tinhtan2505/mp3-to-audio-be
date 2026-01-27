import os
import time
import re
import librosa
import soundfile as sf
import numpy as np
import pysrt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import SAMPLE_RATE, VOICE_MALE, VOICE_FEMALE
from utils import Logger, generate_tts, get_timestamp_str
import asyncio

router = APIRouter()

class TtsBatchFromFilesRequest(BaseModel):
    input_srt_path: str
    audio_files_dir: str = None  # Nếu None, sẽ dùng thư mục tts ngang hàng với SRT
    batch_size: int = None  # Override batch size nếu cần


def get_available_audio_files(audio_dir):
    """Quét thư mục và tạo dict mapping index -> file path"""
    audio_map = {}
    if not os.path.exists(audio_dir):
        return audio_map

    for filename in os.listdir(audio_dir):
        if filename.endswith('.mp3'):
            # Lấy số từ tên file (VD: "1.mp3" -> 1, "0001.mp3" -> 1)
            match = re.match(r'(\d+)\.mp3$', filename)
            if match:
                index = int(match.group(1))
                audio_map[index] = os.path.join(audio_dir, filename)

    return audio_map


# --- BATCH TTS TỪ CÁC FILE MP3 CÓ SẴN ---
@router.post("/api/v1/dubbing/tts-batch-from-files")
async def api_tts_batch_from_files(req: TtsBatchFromFilesRequest):
    start_time = time.time()
    try:
        path = os.path.abspath(req.input_srt_path)
        if not os.path.exists(path):
            raise ValueError(f"File SRT không tồn tại: {path}")

        subs = pysrt.open(path)
        if not subs:
            raise ValueError("SRT rỗng")

        total_subs = len(subs)

        # Xác định thư mục chứa file audio - mặc định là thư mục "tts" ngang hàng với SRT
        if req.audio_files_dir:
            audio_dir = os.path.abspath(req.audio_files_dir)
        else:
            srt_dir = os.path.dirname(path)
            audio_dir = os.path.join(srt_dir, "tts")

        if not os.path.exists(audio_dir):
            raise ValueError(f"Thư mục audio không tồn tại: {audio_dir}")

        # Quét và map các file audio có sẵn
        audio_map = get_available_audio_files(audio_dir)
        print(f"\n📂 Quét thư mục audio: {audio_dir}")
        print(f"   • Tìm thấy {len(audio_map)} file MP3")
        if audio_map:
            indices = sorted(audio_map.keys())
            print(f"   • Index range: {indices[0]} - {indices[-1]}")

        # Tự động tính BATCH_SIZE
        if total_subs < 100:
            BATCH_SIZE = 20
        elif total_subs < 500:
            BATCH_SIZE = 30
        elif total_subs < 1000:
            BATCH_SIZE = 40
        elif total_subs < 3000:
            BATCH_SIZE = 50
        else:
            BATCH_SIZE = 60

        # Override từ request nếu có
        if req.batch_size:
            BATCH_SIZE = min(req.batch_size, 100)

        SAFETY_GAP = 0.1
        MAX_SPEED_UP = 60
        CHECKPOINT_INTERVAL = 500

        total_seconds = (subs[-1].end.ordinal / 1000.0) + 20.0
        final_audio = np.zeros(int(total_seconds * SAMPLE_RATE), dtype=np.float32)

        Logger.section("TTS BATCH FROM FILES - LARGE FILE")
        print(f"   • File SRT: {os.path.basename(path)}")
        print(f"   • Thư mục audio: {audio_dir}")
        print(f"   • Tổng câu: {total_subs:,}")
        print(f"   • Batch size: {BATCH_SIZE} câu/lần")
        print(f"   • Số batch: {(total_subs + BATCH_SIZE - 1) // BATCH_SIZE}\n")

        processed_count = 0
        missing_files = []
        last_end_sample = 0
        last_checkpoint_sample = 0

        # Nhóm các subtitle theo batch
        for batch_start in range(0, total_subs, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_subs)
            batch_subs = subs[batch_start:batch_end]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (total_subs + BATCH_SIZE - 1) // BATCH_SIZE

            print(f"\n📦 Batch {batch_num}/{total_batches} | Câu {batch_start+1}-{batch_end} | Tiến độ: {(batch_end/total_subs*100):.1f}%")

            # Chuẩn bị data cho batch
            batch_data = []
            for i, sub in enumerate(batch_subs):
                txt_raw = sub.text.strip()
                clean_txt = re.sub(r"^\[.*?\]", "", txt_raw).strip()
                if not clean_txt:
                    continue

                is_male = "[NAM" in txt_raw.upper() or "[M]" in txt_raw.upper()
                voice = VOICE_MALE if is_male else VOICE_FEMALE

                start_sec = sub.start.ordinal / 1000.0
                end_sec = sub.end.ordinal / 1000.0

                global_idx = batch_start + i

                # SRT index bắt đầu từ 1, không phải 0
                srt_index = global_idx + 1

                if global_idx < total_subs - 1:
                    next_start = subs[global_idx + 1].start.ordinal / 1000.0
                    hard_limit = next_start - SAFETY_GAP
                else:
                    hard_limit = end_sec + 5.0
                hard_limit = max(hard_limit, end_sec)

                # Tìm file audio từ audio_map dựa trên SRT index
                audio_file = audio_map.get(srt_index)

                batch_data.append({
                    'array_index': global_idx,  # Index trong mảng (0-based)
                    'srt_index': srt_index,      # Index trong SRT (1-based)
                    'text': clean_txt,
                    'voice': voice,
                    'is_male': is_male,
                    'start_sec': start_sec,
                    'end_sec': end_sec,
                    'slot_duration': end_sec - start_sec,
                    'hard_limit': hard_limit,
                    'audio_file': audio_file
                })

            if not batch_data:
                continue

            # Kiểm tra file tồn tại
            batch_start_time = time.time()
            missing_in_batch = []
            for item in batch_data:
                if item['audio_file'] is None or not os.path.exists(item['audio_file']):
                    missing_in_batch.append(item['srt_index'])
                    missing_files.append(item['srt_index'])

            if missing_in_batch:
                print(f"   ⚠️ Thiếu {len(missing_in_batch)} file MP3 trong batch này:")
                for idx in missing_in_batch[:10]:  # Hiển thị tối đa 10 file
                    print(f"      - Index {idx}: {idx}.mp3")
                if len(missing_in_batch) > 10:
                    print(f"      ... và {len(missing_in_batch) - 10} file khác")

            # Xử lý audio cho từng câu
            for idx, item in enumerate(batch_data):
                if item['audio_file'] is None or not os.path.exists(item['audio_file']):
                    print(f"   [❌ {item['srt_index']:04d}] File không tồn tại: {item['srt_index']}.mp3")
                    continue

                try:
                    # Load audio
                    y, _ = librosa.load(item['audio_file'], sr=SAMPLE_RATE)
                    y_trimmed, _ = librosa.effects.trim(y, top_db=30)
                    dur_original = len(y_trimmed) / SAMPLE_RATE

                    # Tính vị trí
                    if batch_start == 0 and idx == 0:
                        actual_start_sample = int(item['start_sec'] * SAMPLE_RATE)
                    else:
                        actual_start_sample = max(
                            int(item['start_sec'] * SAMPLE_RATE),
                            last_end_sample
                        )

                    actual_start_sec = actual_start_sample / SAMPLE_RATE
                    available_space = item['hard_limit'] - actual_start_sec

                    # Xử lý tăng tốc nếu cần
                    y_final = y_trimmed
                    status_log = "✓"
                    speedup_info = ""

                    if dur_original > available_space and available_space >= 0.5:
                        needed_ratio = (dur_original / available_space) - 1.0
                        needed_percent = min(int(needed_ratio * 100) + 5, MAX_SPEED_UP)
                        final_rate_str = f"+{needed_percent}%"

                        print(f"   [⚡ {item['srt_index']:04d}] Tăng tốc {needed_percent}% | Gốc: {dur_original:.2f}s > Slot: {available_space:.2f}s")

                        # Tạo lại với tốc độ nhanh hơn
                        speedup_file = f"speedup_{item['srt_index']}.mp3"
                        try:
                            await generate_tts(item['text'], item['voice'], speedup_file, rate=final_rate_str)
                            y, _ = librosa.load(speedup_file, sr=SAMPLE_RATE)
                            y_final, _ = librosa.effects.trim(y, top_db=30)
                            status_log = f"⚡{needed_percent}%"
                            speedup_info = f" → {len(y_final)/SAMPLE_RATE:.2f}s"
                        finally:
                            if os.path.exists(speedup_file):
                                os.remove(speedup_file)

                    # Ghép audio vào final_audio
                    end_sample = actual_start_sample + len(y_final)
                    if end_sample > len(final_audio):
                        padding = np.zeros(end_sample - len(final_audio) + SAMPLE_RATE, dtype=np.float32)
                        final_audio = np.concatenate((final_audio, padding))

                    final_audio[actual_start_sample:end_sample] += y_final
                    last_end_sample = end_sample
                    processed_count += 1

                    # Log mỗi 10 câu hoặc câu cuối
                    if (idx + 1) % 10 == 0 or idx == len(batch_data) - 1:
                        progress_pct = (processed_count / total_subs * 100)
                        print(f"   [{item['srt_index']:04d}] {status_log} | {dur_original:.2f}s{speedup_info} | Progress: {processed_count}/{total_subs} ({progress_pct:.1f}%)")

                except Exception as e:
                    print(f"   [❌ {item['srt_index']:04d}] Lỗi xử lý: {e}")
                    continue

            batch_time = time.time() - batch_start_time
            print(f"   ⏱️ Batch time: {batch_time:.1f}s | Avg: {batch_time/len(batch_data):.2f}s/câu")

            # Checkpoint mỗi CHECKPOINT_INTERVAL câu
            if processed_count % CHECKPOINT_INTERVAL == 0 and processed_count > 0:
                checkpoint_file = f"{path.replace('.srt', '')}_checkpoint_{processed_count}.wav"
                checkpoint_audio = final_audio[:last_end_sample]
                sf.write(checkpoint_file, checkpoint_audio, SAMPLE_RATE)
                print(f"\n💾 Checkpoint saved: {os.path.basename(checkpoint_file)}")
                last_checkpoint_sample = last_end_sample

        # Lưu file cuối
        final_valid_len = max(last_end_sample, int(subs[-1].end.ordinal/1000 * SAMPLE_RATE))
        final_audio = final_audio[:final_valid_len + int(0.5*SAMPLE_RATE)]
        out_name = f"{path.replace('.srt', '')}_audio_final_{get_timestamp_str()}.wav"
        sf.write(out_name, final_audio, SAMPLE_RATE)

        elapsed = time.time() - start_time

        # Tổng kết
        Logger.success(f"TTS hoàn tất: {os.path.basename(out_name)}", elapsed)
        print(f"\n📊 THỐNG KÊ TỔNG KẾT:")
        print(f"   • Tổng câu trong SRT: {total_subs:,}")
        print(f"   • Câu xử lý thành công: {processed_count:,}")
        print(f"   • Câu thiếu file MP3: {len(missing_files):,}")
        print(f"   • Tỷ lệ thành công: {(processed_count/total_subs*100):.1f}%")
        print(f"   • Thời gian: {elapsed:.1f}s ({elapsed/60:.1f} phút)")
        print(f"   • Tốc độ: {processed_count/elapsed:.1f} câu/giây")

        if missing_files:
            print(f"\n⚠️ DANH SÁCH FILE THIẾU ({len(missing_files)} file):")
            # Hiển thị tối đa 50 file đầu
            for idx in sorted(missing_files)[:50]:
                print(f"   - Index {idx}: {idx}.mp3")
            if len(missing_files) > 50:
                print(f"   ... và {len(missing_files) - 50} file khác")
            print(f"\n💡 Hãy tạo các file MP3 còn thiếu trong thư mục: {audio_dir}")

        return {
            "status": "success" if processed_count == total_subs else "partial_success",
            "output_file": out_name,
            "processed": processed_count,
            "total": total_subs,
            "missing_files": len(missing_files),
            "missing_indices": sorted(missing_files),
            "elapsed_seconds": elapsed,
            "batch_size": BATCH_SIZE,
            "success_rate": round(processed_count / total_subs * 100, 2),
            "audio_directory": audio_dir
        }

    except Exception as e:
        Logger.error("Lỗi TTS Batch From Files", e)
        raise HTTPException(500, str(e))