import os
import time
import uuid
import re
import librosa
import soundfile as sf
import numpy as np
import pysrt
from fastapi import APIRouter, HTTPException
from schemas import TtsRequest
from config import SAMPLE_RATE, VOICE_MALE, VOICE_FEMALE
from utils import Logger, generate_tts, get_timestamp_str
from concurrent.futures import ThreadPoolExecutor
import asyncio

router = APIRouter()

# --- BATCH TTS CHO FILE LỚN (5000+ câu) ---
@router.post("/api/v1/dubbing/tts-gen")
async def api_tts_batch_large(req: TtsRequest):
    start_time = time.time()
    try:
        path = os.path.abspath(req.input_srt_path)
        subs = pysrt.open(path)
        if not subs: raise ValueError("SRT rỗng")

        total_subs = len(subs)

        # Tự động tính BATCH_SIZE cho file lớn
        if total_subs < 100:
            BATCH_SIZE = 20
        elif total_subs < 500:
            BATCH_SIZE = 30
        elif total_subs < 1000:
            BATCH_SIZE = 40
        elif total_subs < 3000:
            BATCH_SIZE = 50
        else:  # >= 3000 câu
            BATCH_SIZE = 60

        # Override từ request nếu có
        if hasattr(req, 'batch_size') and req.batch_size:
            BATCH_SIZE = min(req.batch_size, 100)

        SAFETY_GAP = 0.1
        MAX_SPEED_UP = 60
        MAX_CONCURRENT_TASKS = 50  # Giới hạn số task đồng thời
        CHECKPOINT_INTERVAL = 500  # Lưu checkpoint mỗi 500 câu

        total_seconds = (subs[-1].end.ordinal / 1000.0) + 20.0
        final_audio = np.zeros(int(total_seconds * SAMPLE_RATE), dtype=np.float32)

        Logger.section("TTS BATCH PROCESSING - LARGE FILE")
        print(f"   • Đầu vào: {os.path.basename(path)}")
        print(f"   • Tổng câu: {total_subs:,}")
        print(f"   • Batch size: {BATCH_SIZE} câu/lần")
        print(f"   • Số batch: {(total_subs + BATCH_SIZE - 1) // BATCH_SIZE}")
        print(f"   • Max concurrent: {MAX_CONCURRENT_TASKS}")
        print(f"   • Ước tính thời gian: ~{(total_subs / BATCH_SIZE * 0.8):.0f}s ({(total_subs / BATCH_SIZE * 0.8 / 60):.1f} phút)\n")

        processed_count = 0
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
                if not clean_txt: continue

                is_male = "[NAM" in txt_raw.upper() or "[M]" in txt_raw.upper()
                voice = VOICE_MALE if is_male else VOICE_FEMALE

                start_sec = sub.start.ordinal / 1000.0
                end_sec = sub.end.ordinal / 1000.0

                global_idx = batch_start + i
                if global_idx < total_subs - 1:
                    next_start = subs[global_idx + 1].start.ordinal / 1000.0
                    hard_limit = next_start - SAFETY_GAP
                else:
                    hard_limit = end_sec + 5.0
                hard_limit = max(hard_limit, end_sec)

                batch_data.append({
                    'index': global_idx,
                    'text': clean_txt,
                    'voice': voice,
                    'is_male': is_male,
                    'start_sec': start_sec,
                    'end_sec': end_sec,
                    'slot_duration': end_sec - start_sec,
                    'hard_limit': hard_limit
                })

            if not batch_data:
                continue

            # TTS song song với giới hạn concurrent
            temp_files = []
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

            async def generate_with_limit(item):
                async with semaphore:
                    tmp_file = f"temp_{uuid.uuid4().hex}.mp3"
                    try:
                        await generate_tts(item['text'], item['voice'], tmp_file, rate="+0%")
                        return tmp_file, None
                    except Exception as e:
                        return tmp_file, e

            # Tạo tasks
            tasks = [generate_with_limit(item) for item in batch_data]

            # Chờ tất cả hoàn thành với timeout
            batch_start_time = time.time()
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=60 * len(batch_data)  # 60s mỗi câu
                )
                temp_files = [r[0] for r in results]
            except asyncio.TimeoutError:
                print(f"⚠️ Batch {batch_num} timeout - bỏ qua và tiếp tục")
                continue

            batch_tts_time = time.time() - batch_start_time
            print(f"   ⏱️ TTS time: {batch_tts_time:.1f}s | Avg: {batch_tts_time/len(batch_data):.2f}s/câu")

            # Xử lý audio
            last_end_sample = last_checkpoint_sample if batch_start == 0 else last_end_sample

            for idx, (item, tmp_file) in enumerate(zip(batch_data, temp_files)):
                if not os.path.exists(tmp_file):
                    continue

                try:
                    y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
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

                    # Xử lý tăng tốc
                    y_final = y_trimmed
                    status_log = "✓"

                    if dur_original > available_space and available_space >= 0.5:
                        needed_ratio = (dur_original / available_space) - 1.0
                        needed_percent = min(int(needed_ratio * 100) + 5, MAX_SPEED_UP)
                        final_rate_str = f"+{needed_percent}%"

                        os.remove(tmp_file)
                        await generate_tts(item['text'], item['voice'], tmp_file, rate=final_rate_str)
                        y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
                        y_final, _ = librosa.effects.trim(y, top_db=30)
                        status_log = f"⚡{needed_percent}%"

                    # Ghép audio
                    end_sample = actual_start_sample + len(y_final)
                    if end_sample > len(final_audio):
                        padding = np.zeros(end_sample - len(final_audio) + SAMPLE_RATE, dtype=np.float32)
                        final_audio = np.concatenate((final_audio, padding))

                    final_audio[actual_start_sample:end_sample] += y_final
                    last_end_sample = end_sample
                    processed_count += 1

                    # Log mỗi 10 câu
                    if (idx + 1) % 10 == 0 or idx == len(batch_data) - 1:
                        print(f"   [{item['index']+1:04d}] {status_log} | Processed: {processed_count}/{total_subs}")

                except Exception as e:
                    print(f"❌ Câu {item['index']+1}: {e}")
                finally:
                    if os.path.exists(tmp_file):
                        os.remove(tmp_file)

            # Checkpoint mỗi CHECKPOINT_INTERVAL câu
            if processed_count % CHECKPOINT_INTERVAL == 0 and processed_count > 0:
                checkpoint_file = f"{path.replace('.srt', '')}_checkpoint_{processed_count}.wav"
                checkpoint_audio = final_audio[:last_end_sample]
                sf.write(checkpoint_file, checkpoint_audio, SAMPLE_RATE)
                print(f"\n💾 Checkpoint saved: {checkpoint_file}")
                last_checkpoint_sample = last_end_sample

        # Lưu file cuối
        final_valid_len = max(last_end_sample, int(subs[-1].end.ordinal/1000 * SAMPLE_RATE))
        final_audio = final_audio[:final_valid_len + int(0.5*SAMPLE_RATE)]
        out_name = f"{path.replace('.srt', '')}_audio_final_{get_timestamp_str()}.wav"
        sf.write(out_name, final_audio, SAMPLE_RATE)

        elapsed = time.time() - start_time
        Logger.success(f"TTS hoàn tất: {out_name}", elapsed)
        print(f"\n📊 Thống kê:")
        print(f"   • Tổng câu xử lý: {processed_count:,}/{total_subs:,}")
        print(f"   • Thời gian: {elapsed:.1f}s ({elapsed/60:.1f} phút)")
        print(f"   • Tốc độ: {processed_count/elapsed:.1f} câu/giây")

        return {
            "status": "success",
            "output_file": out_name,
            "processed": processed_count,
            "total": total_subs,
            "elapsed_seconds": elapsed,
            "batch_size": BATCH_SIZE
        }

    except Exception as e:
        Logger.error("Lỗi TTS Batch Large", e)
        raise HTTPException(500, str(e))