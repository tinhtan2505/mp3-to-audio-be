import os
import time
import pysrt
import threading
import uuid
import re
import asyncio
import librosa
import soundfile as sf
import numpy as np
from fastapi import APIRouter, HTTPException
from schemas import WhisperRequest
from google.genai.types import GenerateContentConfig
from google import genai
from ai_core import AI_MODELS
from config import (
    WHISPER_BACKEND, MAX_SEGMENTS_PER_FILE, TRANS_BATCH_SIZE,
    TRANS_DELAY_SECONDS_GEMINI, SYSTEM_INSTRUCTION_TRANS_GEMINI,
    GEMINI_API_KEYS, VOICE_MALE, VOICE_FEMALE, SAMPLE_RATE
)
from utils import Logger, get_timestamp_str, normalize_segment_time, generate_tts

router = APIRouter()

# === GLOBAL STATE CHO KEY ROTATION ===
GEMINI_STATE = {
    'current_key_index': 0,
    'failed_keys': set(),
    'lock': threading.Lock()
}


def get_next_gemini_client():
    """
    Lấy Gemini client với key tiếp theo (skip các key đã fail)
    Trả về: (client, key_index) hoặc (None, -1)
    """
    with GEMINI_STATE['lock']:
        if not GEMINI_API_KEYS:
            return None, -1

        start_index = GEMINI_STATE['current_key_index']
        attempts = 0
        total_keys = len(GEMINI_API_KEYS)

        while attempts < total_keys:
            current_index = (start_index + attempts) % total_keys

            # Skip key đã fail
            if current_index in GEMINI_STATE['failed_keys']:
                attempts += 1
                continue

            key = GEMINI_API_KEYS[current_index]
            if not key or "AIza" not in key:
                GEMINI_STATE['failed_keys'].add(current_index)
                attempts += 1
                continue

            try:
                client = genai.Client(api_key=key)
                GEMINI_STATE['current_key_index'] = current_index
                return client, current_index
            except Exception as e:
                print(f"      ⚠️  Key #{current_index+1} khởi tạo thất bại: {str(e)[:50]}")
                GEMINI_STATE['failed_keys'].add(current_index)
                attempts += 1

        return None, -1


def mark_key_as_failed(key_index):
    """Đánh dấu key đã fail và chuyển sang key tiếp theo"""
    with GEMINI_STATE['lock']:
        GEMINI_STATE['failed_keys'].add(key_index)
        print(f"      ❌ Key #{key_index+1} đã bị đánh dấu thất bại (quota/error)")


def rotate_to_next_key():
    """Chuyển sang key tiếp theo"""
    with GEMINI_STATE['lock']:
        if not GEMINI_API_KEYS:
            return None, -1

        current = GEMINI_STATE['current_key_index']
        total_keys = len(GEMINI_API_KEYS)

        # Thử tối đa tất cả các key
        for offset in range(1, total_keys + 1):
            next_index = (current + offset) % total_keys

            if next_index in GEMINI_STATE['failed_keys']:
                continue

            key = GEMINI_API_KEYS[next_index]
            if not key or "AIza" not in key:
                GEMINI_STATE['failed_keys'].add(next_index)
                continue

            try:
                client = genai.Client(api_key=key)
                GEMINI_STATE['current_key_index'] = next_index
                masked = f"{key[:5]}...{key[-4:]}"
                print(f"\n      🔄 Đã chuyển sang Key #{next_index+1}: {masked}")
                return client, next_index
            except Exception as e:
                print(f"      ⚠️  Key #{next_index+1} khởi tạo thất bại: {str(e)[:50]}")
                GEMINI_STATE['failed_keys'].add(next_index)
                continue

        return None, -1


def write_srt_line(file_handle, index, start, end, text):
    """Ghi 1 dòng SRT vào file ngay lập tức"""
    file_handle.write(f"{index}\n{start} --> {end}\n{text}\n\n")
    file_handle.flush()


def format_timestamp(seconds):
    """Chuyển seconds thành format SRT: 00:00:00,000"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def call_gemini_api(text_list):
    """
    Gửi yêu cầu dịch KHÔNG retry - chỉ gọi 1 lần
    Nếu lỗi 429 thì rotate key và return None ngay
    """
    if not GEMINI_API_KEYS:
        return None

    prompt_content = "Dịch danh sách các dòng thoại sau sang Tiếng Việt (giữ nguyên số lượng dòng):\n"
    for i, txt in enumerate(text_list):
        prompt_content += f"Line_{i}: {txt}\n"

    current_client = AI_MODELS.get("gemini_client")
    current_key_index = GEMINI_STATE['current_key_index']

    try:
        # Nếu client chưa có, lấy client mới
        if not current_client:
            current_client, current_key_index = get_next_gemini_client()
            if not current_client:
                print(f"      ❌ Không còn key khả dụng")
                return None
            AI_MODELS["gemini_client"] = current_client

        time.sleep(TRANS_DELAY_SECONDS_GEMINI)

        response = current_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt_content,
            config=GenerateContentConfig(
                temperature=0.1,
                system_instruction=SYSTEM_INSTRUCTION_TRANS_GEMINI
            )
        )

        if not hasattr(response, 'text'):
            print(f"      ⚠️  Response không có text")
            return None

        raw_text = response.text.strip()
        translated_lines = []

        for line in raw_text.split('\n'):
            clean_line = line.strip()
            if ":" in clean_line and (clean_line.startswith("Line") or clean_line[0].isdigit()):
                clean_line = clean_line.split(":", 1)[1].strip()
            elif len(clean_line) > 2 and clean_line[0].isdigit() and clean_line[1] in ['.', ')']:
                clean_line = clean_line.split(' ', 1)[1].strip()
            if clean_line:
                translated_lines.append(clean_line)

        return translated_lines

    except Exception as e:
        error_msg = str(e).lower()

        # Phát hiện lỗi 429 (quota exceeded) -> rotate key
        if "429" in error_msg or "quota" in error_msg or "resource_exhausted" in error_msg:
            print(f"      ⚠️  Key #{current_key_index+1} hết quota (429) - Đánh dấu key fail")
            mark_key_as_failed(current_key_index)

            # Rotate sang key tiếp theo cho lần gọi sau
            next_client, next_key_index = rotate_to_next_key()
            if next_client:
                AI_MODELS["gemini_client"] = next_client
            else:
                print(f"      ❌ TẤT CẢ KEY ĐỀU HẾT QUOTA")

            return None

        # Các lỗi khác
        print(f"      ❌ Gemini API lỗi: {str(e)[:100]}")
        return None


def translate_srt_file_simple(input_srt_path):
    """
    Dịch file SRT sang tiếng Việt với auto key rotation
    """
    translate_start = time.time()

    if not GEMINI_API_KEYS:
        print(f"   ⚠️  Bỏ qua dịch: Không có Gemini API key")
        return None

    try:
        print(f"\n{'='*70}")
        print(f"🌐 BẮT ĐẦU DỊCH FILE SANG TIẾNG VIỆT")
        print(f"{'='*70}")
        print(f"   📂 File gốc: {os.path.basename(input_srt_path)}")

        # Tạo tên file đầu ra
        dir_name, base_name = os.path.split(input_srt_path)
        output_path = os.path.join(dir_name, f"{os.path.splitext(base_name)[0]}_vi.srt")

        # Đọc file SRT
        try:
            subs = pysrt.open(input_srt_path)
        except:
            subs = pysrt.open(input_srt_path, encoding='utf-8')

        total_subs = len(subs)
        print(f"   📚 Tổng số dòng thoại: {total_subs}")
        print(f"   📦 Kích thước lô: {TRANS_BATCH_SIZE} dòng/lô")
        print(f"   🔑 Số key khả dụng: {len(GEMINI_API_KEYS) - len(GEMINI_STATE['failed_keys'])}/{len(GEMINI_API_KEYS)}")
        print(f"   ⏱️  Thời gian bắt đầu: {time.strftime('%H:%M:%S')}\n")

        # Thống kê
        total_translated = 0
        total_failed = 0
        total_mismatched = 0
        has_errors = False

        # Dịch từng batch
        for i in range(0, total_subs, TRANS_BATCH_SIZE):
            batch_start = time.time()
            current_batch = subs[i : i + TRANS_BATCH_SIZE]
            batch_size = len(current_batch)

            print(f"   🔄 Đang dịch lô {(i//TRANS_BATCH_SIZE)+1}/{(total_subs-1)//TRANS_BATCH_SIZE+1} (dòng {i+1}-{min(i + TRANS_BATCH_SIZE, total_subs)})...")

            # Lấy text gốc
            original_texts = [sub.text for sub in current_batch]

            # Gọi API dịch (có auto retry + key rotation)
            translated_texts = call_gemini_api(original_texts)

            # Xử lý kết quả
            if translated_texts is None:
                print(f"      ⚠️  LỖI API: Giữ nguyên {batch_size} dòng gốc")
                total_failed += batch_size
                has_errors = True
            elif len(translated_texts) != batch_size:
                print(f"      ⚠️  LỆCH DÒNG: Nhận {len(translated_texts)}/{batch_size} dòng - Giữ nguyên text gốc")
                total_mismatched += batch_size
                has_errors = True
            else:
                # Thành công - cập nhật text
                for j, new_text in enumerate(translated_texts):
                    if i + j < total_subs:
                        subs[i + j].text = new_text
                total_translated += batch_size
                print(f"      ✓ Hoàn thành: {batch_size} dòng trong {time.time() - batch_start:.2f}s")

            # Lưu tạm sau mỗi batch
            subs.save(output_path, encoding='utf-8')

        # Đổi tên file nếu có lỗi
        if has_errors:
            error_path = output_path.replace('.srt', '_[ERROR].srt')
            os.rename(output_path, error_path)
            output_path = error_path

        translate_elapsed = time.time() - translate_start

        print(f"\n{'='*70}")
        print(f"✅ DỊCH HOÀN TẤT")
        print(f"{'='*70}")
        print(f"   📝 File đầu ra: {os.path.basename(output_path)}")
        print(f"   📊 Tổng số dòng: {total_subs}")
        print(f"   ✓ Dịch thành công: {total_translated}")
        print(f"   ⚠️  Lỗi API: {total_failed}")
        print(f"   ⚠️  Lệch dòng: {total_mismatched}")
        print(f"   🔑 Key đã dùng: {GEMINI_STATE['current_key_index']+1}/{len(GEMINI_API_KEYS)}")
        print(f"   ⏱️  Thời gian dịch: {translate_elapsed:.2f}s ({translate_elapsed/60:.1f} phút)")
        if total_translated > 0:
            print(f"   ⚡ Tốc độ: {total_translated/(translate_elapsed/60):.1f} dòng/phút")
        print(f"{'='*70}\n")

        # Trả về None nếu có lỗi, để không trigger TTS
        if has_errors:
            return None

        return output_path

    except Exception as e:
        print(f"\n{'='*70}")
        print(f"❌ LỖI NGHIÊM TRỌNG TRONG QUÁ TRÌNH DỊCH")
        print(f"{'='*70}")
        print(f"   🔴 Lỗi: {str(e)}")
        print(f"   📂 File: {os.path.basename(input_srt_path)}")
        print(f"{'='*70}\n")
        return None


# ============================================================================
# TTS FUNCTIONS - XỬ LÝ THỜI GIAN & TĂNG TỐC AUDIO
# ============================================================================

async def generate_tts_with_speedup(text, voice, output_file, available_space, rate="+0%"):
    """
    Tạo TTS với tự động tăng tốc nếu audio quá dài

    Args:
        text: Nội dung cần TTS
        voice: Giọng đọc
        output_file: Đường dẫn file MP3 đầu ra
        available_space: Thời gian khả dụng (giây)
        rate: Tốc độ ban đầu

    Returns:
        dict: {
            'success': bool,
            'duration': float,  # Thời lượng audio thực tế
            'speedup_percent': int,  # % tăng tốc đã áp dụng
            'status': str  # ✓ hoặc ⚡X%
        }
    """
    MAX_SPEED_UP = 60
    tmp_file = f"temp_{uuid.uuid4().hex}.mp3"

    try:
        # Bước 1: TTS với tốc độ ban đầu
        await generate_tts(text, voice, tmp_file, rate=rate)

        # Bước 2: Load và trim audio
        y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
        y_trimmed, _ = librosa.effects.trim(y, top_db=30)
        dur_original = len(y_trimmed) / SAMPLE_RATE

        # Bước 3: Kiểm tra có cần tăng tốc không
        speedup_percent = 0
        status = "✓"

        if dur_original > available_space and available_space >= 0.5:
            # Tính tốc độ cần tăng
            needed_ratio = (dur_original / available_space) - 1.0
            speedup_percent = min(int(needed_ratio * 100) + 5, MAX_SPEED_UP)
            final_rate_str = f"+{speedup_percent}%"

            # TTS lại với tốc độ mới
            os.remove(tmp_file)
            await generate_tts(text, voice, tmp_file, rate=final_rate_str)

            # Load lại audio đã tăng tốc
            y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
            y_trimmed, _ = librosa.effects.trim(y, top_db=30)

            status = f"⚡{speedup_percent}%"

        # Bước 4: Lưu file cuối cùng
        final_duration = len(y_trimmed) / SAMPLE_RATE

        # Chuyển sang file đầu ra cuối cùng
        if os.path.exists(output_file):
            os.remove(output_file)
        os.rename(tmp_file, output_file)

        return {
            'success': True,
            'duration': final_duration,
            'speedup_percent': speedup_percent,
            'status': status,
            'audio_data': y_trimmed  # Có thể dùng để ghép file tổng sau này
        }

    except Exception as e:
        print(f"      ❌ TTS error: {str(e)[:100]}")
        if os.path.exists(tmp_file):
            os.remove(tmp_file)
        return {
            'success': False,
            'duration': 0,
            'speedup_percent': 0,
            'status': '❌',
            'audio_data': None
        }


async def tts_batch_with_timing(vi_srt_path, tts_files_list, lock):
    """
    Xử lý TTS cho file VI với LOGIC THỜI GIAN & TĂNG TỐC
    TẠO TỪNG FILE MP3 ĐÃ XỬ LÝ THỜI GIAN, SẴN SÀNG ĐỂ GHÉP
    """
    start_time = time.time()

    try:
        # Tạo thư mục tts
        srt_dir = os.path.dirname(vi_srt_path)
        tts_dir = os.path.join(srt_dir, "tts")
        os.makedirs(tts_dir, exist_ok=True)

        print(f"\n{'='*70}")
        print(f"🎤 BẮT ĐẦU TẠO TTS VỚI XỬ LÝ THỜI GIAN")
        print(f"{'='*70}")
        print(f"   📂 File VI: {os.path.basename(vi_srt_path)}")
        print(f"   📁 Thư mục TTS: {tts_dir}")

        # Đọc file SRT
        try:
            subs = pysrt.open(vi_srt_path)
        except:
            subs = pysrt.open(vi_srt_path, encoding='utf-8')

        if not subs:
            print(f"   ⚠️  File SRT rỗng - bỏ qua TTS")
            return

        total_subs = len(subs)

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

        MAX_CONCURRENT_TASKS = 50
        SAFETY_GAP = 0.1  # Khoảng cách an toàn giữa các câu

        print(f"   • Tổng câu: {total_subs:,}")
        print(f"   • Batch size: {BATCH_SIZE} câu/lần")
        print(f"   • Số batch: {(total_subs + BATCH_SIZE - 1) // BATCH_SIZE}")
        print(f"   • Max concurrent: {MAX_CONCURRENT_TASKS}")
        print(f"   • Safety gap: {SAFETY_GAP}s")
        print(f"   • Ước tính thời gian: ~{(total_subs / BATCH_SIZE * 0.8):.0f}s ({(total_subs / BATCH_SIZE * 0.8 / 60):.1f} phút)\n")

        processed_count = 0
        success_count = 0
        failed_count = 0
        speedup_count = 0
        total_speedup_percent = 0

        # Tạo file JSON metadata để lưu thông tin timing
        metadata_file = os.path.join(tts_dir, "timing_metadata.json")
        metadata = {}

        # Xử lý từng batch
        for batch_start in range(0, total_subs, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_subs)
            batch_subs = subs[batch_start:batch_end]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (total_subs + BATCH_SIZE - 1) // BATCH_SIZE

            print(f"\n📦 Batch {batch_num}/{total_batches} | Câu {batch_start+1}-{batch_end} | Tiến độ: {(batch_end/total_subs*100):.1f}%")

            # Chuẩn bị data cho batch với LOGIC THỜI GIAN
            batch_data = []
            for i, sub in enumerate(batch_subs):
                txt_raw = sub.text.strip()
                clean_txt = re.sub(r"^\[.*?\]", "", txt_raw).strip()
                if not clean_txt:
                    continue

                is_male = "[NAM" in txt_raw.upper() or "[M]" in txt_raw.upper()
                voice = VOICE_MALE if is_male else VOICE_FEMALE

                # Tính toán thời gian
                start_sec = sub.start.ordinal / 1000.0
                end_sec = sub.end.ordinal / 1000.0
                slot_duration = end_sec - start_sec

                # Tính hard_limit (thời gian tối đa có thể dùng)
                global_idx = batch_start + i
                if global_idx < total_subs - 1:
                    next_start = subs[global_idx + 1].start.ordinal / 1000.0
                    hard_limit = next_start - SAFETY_GAP
                else:
                    hard_limit = end_sec + 5.0
                hard_limit = max(hard_limit, end_sec)

                available_space = hard_limit - start_sec

                # Tên file: sử dụng index thực tế từ SRT file
                output_filename = f"{sub.index}.mp3"
                output_path = os.path.join(tts_dir, output_filename)

                batch_data.append({
                    'index': sub.index,
                    'text': clean_txt,
                    'voice': voice,
                    'is_male': is_male,
                    'start_sec': start_sec,
                    'end_sec': end_sec,
                    'slot_duration': slot_duration,
                    'hard_limit': hard_limit,
                    'available_space': available_space,
                    'output_path': output_path
                })

            if not batch_data:
                continue

            # TTS song song với giới hạn concurrent
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

            async def generate_with_limit(item):
                async with semaphore:
                    try:
                        result = await generate_tts_with_speedup(
                            item['text'],
                            item['voice'],
                            item['output_path'],
                            item['available_space'],
                            rate="+0%"
                        )
                        return item['index'], result
                    except Exception as e:
                        return item['index'], {
                            'success': False,
                            'duration': 0,
                            'speedup_percent': 0,
                            'status': '❌',
                            'audio_data': None
                        }

            # Tạo tasks
            tasks = [generate_with_limit(item) for item in batch_data]

            # Chờ tất cả hoàn thành với timeout
            batch_start_time = time.time()
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=60 * len(batch_data)
                )

                # Xử lý kết quả
                for idx, result in results:
                    processed_count += 1

                    if result['success']:
                        success_count += 1

                        # Lưu metadata
                        item = next((x for x in batch_data if x['index'] == idx), None)
                        if item:
                            metadata[str(idx)] = {
                                'start_sec': item['start_sec'],
                                'end_sec': item['end_sec'],
                                'slot_duration': item['slot_duration'],
                                'hard_limit': item['hard_limit'],
                                'available_space': item['available_space'],
                                'actual_duration': result['duration'],
                                'speedup_percent': result['speedup_percent'],
                                'status': result['status']
                            }

                        if result['speedup_percent'] > 0:
                            speedup_count += 1
                            total_speedup_percent += result['speedup_percent']

                        # Thêm vào danh sách kết quả
                        output_file = os.path.join(tts_dir, f"{idx}.mp3")
                        if os.path.exists(output_file):
                            with lock:
                                tts_files_list.append(output_file)
                    else:
                        failed_count += 1

            except asyncio.TimeoutError:
                print(f"⚠️ Batch {batch_num} timeout - bỏ qua và tiếp tục")
                failed_count += len(batch_data)
                continue

            batch_tts_time = time.time() - batch_start_time
            print(f"   ⏱️  TTS time: {batch_tts_time:.1f}s | Avg: {batch_tts_time/len(batch_data):.2f}s/câu")
            print(f"   ✓ Thành công: {success_count} | ⚡ Tăng tốc: {speedup_count} | ❌ Thất bại: {failed_count}")
            print(f"   📊 Đã xử lý: {processed_count}/{total_subs}")

        # Lưu metadata (MERGE với metadata cũ nếu có)
        import json

        # Load metadata cũ nếu file đã tồn tại
        existing_metadata = {}
        if os.path.exists(metadata_file):
            try:
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    existing_metadata = json.load(f)
                print(f"   📖 Đã load {len(existing_metadata):,} entries từ metadata cũ")
            except:
                pass  # Nếu file lỗi thì bỏ qua

        # Merge metadata mới vào metadata cũ
        existing_metadata.update(metadata)

        # Lưu metadata đầy đủ
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(existing_metadata, f, indent=2, ensure_ascii=False)

        print(f"   💾 Đã lưu {len(existing_metadata):,} entries vào metadata (+ {len(metadata):,} mới)")

        elapsed = time.time() - start_time
        avg_speedup = total_speedup_percent / speedup_count if speedup_count > 0 else 0

        print(f"\n{'='*70}")
        print(f"✅ TTS VỚI XỬ LÝ THỜI GIAN HOÀN TẤT")
        print(f"{'='*70}")
        print(f"   📁 Thư mục TTS: {tts_dir}")
        print(f"   📊 Tổng câu xử lý: {processed_count:,}/{total_subs:,}")
        print(f"   ✓ Thành công: {success_count:,} file MP3")
        print(f"   ⚡ Đã tăng tốc: {speedup_count:,} file (trung bình: {avg_speedup:.1f}%)")
        print(f"   ❌ Thất bại: {failed_count:,} file")
        print(f"   📄 Metadata: {os.path.basename(metadata_file)}")
        print(f"   ⏱️  Thời gian: {elapsed:.1f}s ({elapsed/60:.1f} phút)")
        if processed_count > 0:
            print(f"   ⚡ Tốc độ: {processed_count/elapsed:.1f} file/giây")
        print(f"{'='*70}\n")

    except Exception as e:
        print(f"\n{'='*70}")
        print(f"❌ LỖI TTS VỚI XỬ LÝ THỜI GIAN")
        print(f"{'='*70}")
        print(f"   🔴 Lỗi: {str(e)}")
        print(f"   📂 File: {os.path.basename(vi_srt_path)}")
        print(f"{'='*70}\n")


def tts_file_background(vi_srt_path, tts_files_list, lock):
    """
    Wrapper để chạy async TTS với xử lý thời gian trong thread riêng
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(tts_batch_with_timing(vi_srt_path, tts_files_list, lock))
        loop.close()
    except Exception as e:
        print(f"   ❌ [Background TTS] Lỗi: {e}\n")


def translate_file_background(file_path, translated_files_list, lock, tts_files_list, tts_lock):
    """
    Hàm chạy trong thread riêng để dịch file mà không block Whisper
    SAU KHI DỊCH THÀNH CÔNG -> TỰ ĐỘNG GỌI TTS VỚI XỬ LÝ THỜI GIAN
    """
    try:
        translated_file = translate_srt_file_simple(file_path)

        if translated_file:
            with lock:
                translated_files_list.append(translated_file)
            print(f"   ✅ [Background] Đã dịch xong: {os.path.basename(translated_file)}\n")

            # ========== TỰ ĐỘNG TẠO TTS VỚI XỬ LÝ THỜI GIAN ==========
            print(f"   🎤 [Background] Bắt đầu TTS với xử lý thời gian: {os.path.basename(translated_file)}\n")
            tts_thread = threading.Thread(
                target=tts_file_background,
                args=(translated_file, tts_files_list, tts_lock),
                daemon=True
            )
            tts_thread.start()
            # ============================================================

        else:
            print(f"   ⚠️  [Background] Dịch thất bại: {os.path.basename(file_path)}\n")
    except Exception as e:
        print(f"   ❌ [Background] Lỗi dịch {os.path.basename(file_path)}: {e}\n")


@router.post("/api/v1/dubbing/whisper")
def api_whisper(req: WhisperRequest):
    if not AI_MODELS["whisper"]:
        raise HTTPException(500, "Model Whisper chưa được tải")

    try:
        path = os.path.abspath(req.input_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"File không tồn tại: {path}")

        Logger.section("WHISPER - TÁCH LỜI THOẠI (TỐI ƯU STREAMING)")
        print(f"   📂 Đầu vào: {os.path.basename(path)} ({os.path.getsize(path) / (1024*1024):.2f} MB)")
        print(f"   ⚙️  Chế độ: Streaming thời gian thực + Tự động chia ({MAX_SEGMENTS_PER_FILE} câu/file)")
        print(f"   🔧 Engine: {WHISPER_BACKEND}")

        # Reset failed keys trước mỗi session mới
        with GEMINI_STATE['lock']:
            GEMINI_STATE['failed_keys'].clear()
            print(f"   🔑 Đã reset danh sách key - Sẵn sàng dùng {len(GEMINI_API_KEYS)} key")

        start_w = time.time()

        if WHISPER_BACKEND == "faster":
            # Chuẩn bị output
            out_dir = os.path.dirname(path)
            base_filename = os.path.splitext(os.path.basename(path))[0].split('_')[0]
            timestamp_str = get_timestamp_str()
            output_files_list = []
            translated_files_list = []
            tts_files_list = []
            translation_lock = threading.Lock()
            tts_lock = threading.Lock()
            translation_threads = []

            # Tracking variables
            total_segments = 0
            filtered_count = 0
            chunk_index = 1
            current_file_handle = None
            current_file_path = None
            segments_in_current_file = 0

            # Tối ưu: sử dụng set cho lookup O(1)
            seen_texts = set()
            last_texts = []

            # Thống kê chi tiết
            stats = {
                'total_duration': 0,
                'total_chars': 0,
                'empty_segments': 0,
                'short_segments': 0,
                'duplicates': 0,
                'files_created': 0,
                'avg_segment_length': 0,
                'last_log_time': start_w,
                'segments_since_log': 0
            }

            print(f"\n{'='*70}")
            print(f"🚀 BẮT ĐẦU CHUYỂN ÂM THANH THÀNH VĂN BẢN...")
            print(f"{'='*70}\n")

            try:
                # Mở file đầu tiên
                part_suffix = f"_part{chunk_index:02d}" if MAX_SEGMENTS_PER_FILE < 9999 else ""
                out_name = f"{base_filename}_cn_{timestamp_str}{part_suffix}.srt"
                current_file_path = os.path.join(out_dir, out_name)
                current_file_handle = open(current_file_path, 'w', encoding='utf-8', buffering=8192)
                output_files_list.append(current_file_path)
                stats['files_created'] = 1

                print(f"   📄 File số {stats['files_created']}: {out_name}")
                print(f"   ⏱️  Thời gian bắt đầu: {time.strftime('%H:%M:%S')}\n")

                # STREAMING TRANSCRIBE
                init_time = time.time()
                segments_gen, info = AI_MODELS["whisper"].transcribe(
                    path,
                    language="zh",
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=400),
                    condition_on_previous_text=False,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    repetition_penalty=1.2,
                    no_speech_threshold=0.6,
                    word_timestamps=True,
                    compression_ratio_threshold=2.0,
                    log_prob_threshold=-1.0,
                    initial_prompt=None
                )

                init_elapsed = time.time() - init_time
                print(f"   ✓ Khởi tạo model: {init_elapsed:.2f}s")
                print(f"   🌍 Ngôn ngữ: {info.language} (độ tin cậy: {info.language_probability:.2%})")
                print(f"   ⏳ Đang xử lý các đoạn văn bản...\n")

                # XỬ LÝ STREAMING
                for seg in segments_gen:
                    # Normalize segment
                    if hasattr(seg, 'start'):
                        start = seg.start
                        end = seg.end
                        text = seg.text.strip()
                    else:
                        normalized = normalize_segment_time(seg)
                        start = normalized['start']
                        end = normalized['end']
                        text = normalized['text'].strip()

                    duration = end - start
                    stats['total_duration'] += duration

                    # Kiểm tra empty
                    if not text:
                        stats['empty_segments'] += 1
                        continue

                    # Kiểm tra quá ngắn
                    if len(text) < 2:
                        stats['short_segments'] += 1
                        continue

                    # Tối ưu: check duplicate bằng set (O(1))
                    text_normalized = text.lower().strip()
                    if text_normalized in seen_texts or text in last_texts[-3:]:
                        stats['duplicates'] += 1
                        filtered_count += 1
                        if stats['duplicates'] % 5 == 0:
                            print(f"   ⚠️  Đã lọc {stats['duplicates']} câu trùng lặp...")
                        continue

                    # Ghi segment hợp lệ
                    total_segments += 1
                    segments_in_current_file += 1
                    stats['total_chars'] += len(text)
                    stats['segments_since_log'] += 1

                    # Cập nhật tracking
                    seen_texts.add(text_normalized)
                    last_texts.append(text)
                    if len(last_texts) > 5:
                        last_texts.pop(0)

                    # Ghi file với index nối tiếp toàn cục
                    start_ts = format_timestamp(start)
                    end_ts = format_timestamp(end)
                    write_srt_line(
                        current_file_handle,
                        total_segments,
                        start_ts,
                        end_ts,
                        text
                    )

                    # Log định kỳ (mỗi 3 giây hoặc 20 segments)
                    now = time.time()
                    if (now - stats['last_log_time'] >= 3.0) or (stats['segments_since_log'] >= 20):
                        elapsed = now - start_w
                        speed = total_segments / (elapsed / 60) if elapsed > 0 else 0
                        progress_pct = (stats['total_duration'] / (elapsed or 1)) * 100

                        print(f"   ⚡ [{total_segments:4d}] {text[:45]}{'...' if len(text)>45 else ''}")
                        print(f"      └─ Tốc độ: {speed:.1f} câu/phút | Thời gian: {elapsed:.1f}s | Tiến độ: ~{min(progress_pct, 99):.0f}%")

                        stats['last_log_time'] = now
                        stats['segments_since_log'] = 0

                    # Auto-split file
                    if segments_in_current_file >= MAX_SEGMENTS_PER_FILE:
                        current_file_handle.close()
                        elapsed = time.time() - start_w

                        print(f"\n   ✅ File số {stats['files_created']} hoàn thành: {segments_in_current_file} câu")
                        print(f"      └─ Đường dẫn: {os.path.basename(current_file_path)}")
                        print(f"      └─ Thời gian: {elapsed:.1f}s\n")

                        # DỊCH FILE TRONG BACKGROUND (sẽ tự động trigger TTS với xử lý thời gian)
                        if GEMINI_API_KEYS:
                            thread = threading.Thread(
                                target=translate_file_background,
                                args=(current_file_path, translated_files_list, translation_lock, tts_files_list, tts_lock),
                                daemon=True
                            )
                            thread.start()
                            translation_threads.append(thread)
                            print(f"   🔄 [Background] Bắt đầu dịch: {os.path.basename(current_file_path)}\n")

                        # Mở file mới
                        chunk_index += 1
                        segments_in_current_file = 0
                        stats['files_created'] += 1

                        part_suffix = f"_part{chunk_index:02d}"
                        out_name = f"{base_filename}_cn_{timestamp_str}{part_suffix}.srt"
                        current_file_path = os.path.join(out_dir, out_name)
                        current_file_handle = open(current_file_path, 'w', encoding='utf-8', buffering=8192)
                        output_files_list.append(current_file_path)

                        print(f"   📄 File số {stats['files_created']}: {out_name}\n")

                # Đóng file cuối
                if current_file_handle:
                    current_file_handle.close()
                    print(f"\n   ✅ File cuối cùng hoàn thành: {segments_in_current_file} câu")

                    # DỊCH FILE CUỐI CÙNG (sẽ tự động trigger TTS với xử lý thời gian)
                    if GEMINI_API_KEYS:
                        thread = threading.Thread(
                            target=translate_file_background,
                            args=(current_file_path, translated_files_list, translation_lock, tts_files_list, tts_lock),
                            daemon=True
                        )
                        thread.start()
                        translation_threads.append(thread)
                        print(f"   🔄 [Background] Bắt đầu dịch: {os.path.basename(current_file_path)}\n")

            except KeyboardInterrupt:
                print(f"\n\n{'='*70}")
                print(f"⚠️  BỊ NGẮT BỞI NGƯỜI DÙNG (Ctrl+C)")
                print(f"{'='*70}")
                print(f"   📊 Đã xử lý: {total_segments} câu trước khi bị ngắt")
                print(f"   ⏱️  Thời gian: {time.time() - start_w:.1f}s")

                if current_file_handle:
                    current_file_handle.close()
                    interrupted_path = current_file_path.replace('.srt', '_INTERRUPTED.srt')
                    os.rename(current_file_path, interrupted_path)
                    output_files_list[-1] = interrupted_path
                    print(f"   💾 Đã lưu file tạm: {os.path.basename(interrupted_path)}\n")

                raise HTTPException(499, "Interrupted by user")

            except Exception as e:
                print(f"\n\n{'='*70}")
                print(f"❌ XẢY RA LỖI")
                print(f"{'='*70}")
                print(f"   🔴 Lỗi: {str(e)}")
                print(f"   📊 Đã xử lý: {total_segments} câu trước khi lỗi")

                if current_file_handle:
                    current_file_handle.close()
                    error_path = current_file_path.replace('.srt', '_ERROR.srt')
                    os.rename(current_file_path, error_path)
                    output_files_list[-1] = error_path
                    print(f"   💾 Đã lưu file tạm: {os.path.basename(error_path)}\n")

                raise

            elapsed = time.time() - start_w
            stats['avg_segment_length'] = stats['total_chars'] / total_segments if total_segments > 0 else 0

            # Chờ tất cả translation threads hoàn thành
            print(f"\n{'='*70}")
            print(f"⏳ Đang chờ các tiến trình dịch và TTS (có xử lý thời gian) hoàn thành...")
            print(f"{'='*70}\n")

            for i, thread in enumerate(translation_threads, 1):
                thread.join(timeout=600)  # Timeout 10 phút
                if thread.is_alive():
                    print(f"   ⚠️  Thread {i} vẫn đang chạy (timeout)")

            # ========== GHI FILE TỔNG (MERGED FILE) ==========
            merged_file_path = None
            if translated_files_list:
                try:
                    with translation_lock:
                        sorted_files = sorted(translated_files_list, key=lambda x: x)

                    success_files = [f for f in sorted_files if '[ERROR]' not in f]
                    error_files = [f for f in sorted_files if '[ERROR]' in f]

                    if success_files:
                        print(f"\n{'='*70}")
                        print(f"📚 BẮT ĐẦU GHI FILE TỔNG (MERGED)")
                        print(f"{'='*70}")
                        print(f"   📊 Tổng số file dịch: {len(sorted_files)}")
                        print(f"   ✅ File thành công: {len(success_files)}")
                        if error_files:
                            print(f"   ❌ File lỗi (bỏ qua): {len(error_files)}")
                            for ef in error_files:
                                print(f"      • {os.path.basename(ef)}")

                        base_name = os.path.basename(success_files[0])
                        merged_name = re.sub(r'_part\d+_vi\.srt$', '_vi_FULL.srt', base_name)
                        merged_file_path = os.path.join(out_dir, merged_name)

                        print(f"   📝 File đầu ra: {merged_name}\n")

                        all_subs = pysrt.SubRipFile()

                        for i, vi_file in enumerate(success_files, 1):
                            print(f"   ⚡ Đang đọc file {i}/{len(success_files)}: {os.path.basename(vi_file)}")

                            try:
                                current_subs = pysrt.open(vi_file, encoding='utf-8')
                            except:
                                current_subs = pysrt.open(vi_file)

                            if current_subs:
                                first_idx = current_subs[0].index
                                last_idx = current_subs[-1].index

                                for sub in current_subs:
                                    new_sub = pysrt.SubRipItem(
                                        index=sub.index,
                                        start=sub.start,
                                        end=sub.end,
                                        text=sub.text
                                    )
                                    all_subs.append(new_sub)

                                print(f"      └─ Đã thêm {len(current_subs)} câu (index {first_idx}-{last_idx}, tổng: {len(all_subs)})")

                        all_subs.save(merged_file_path, encoding='utf-8')

                        print(f"\n   ✅ Hoàn thành ghi file tổng")
                        print(f"   📊 Tổng số câu: {len(all_subs):,}")
                        if error_files:
                            print(f"   ⚠️  Lưu ý: Đã bỏ qua {len(error_files)} file lỗi")
                        print(f"   💾 Đường dẫn: {merged_file_path}")
                        print(f"{'='*70}\n")

                    else:
                        print(f"\n   ⚠️  Không có file thành công để merge (tất cả đều có [ERROR])\n")

                except Exception as e:
                    print(f"\n   ❌ Lỗi khi merge file: {str(e)}\n")

            # Final report
            print(f"\n{'='*70}")
            print(f"✅ HOÀN TẤT - FILE TTS ĐÃ SẴN SÀNG ĐỂ GHÉP")
            print(f"{'='*70}")
            print(f"📊 THỐNG KÊ:")
            print(f"   • Ngôn ngữ: {info.language} (độ tin cậy: {info.language_probability:.2%})")
            print(f"   • Tổng số câu: {total_segments:,}")
            print(f"   • Số file TTS MP3: {len(tts_files_list)} (đã xử lý thời gian)")
            print(f"   • Metadata timing: tts/timing_metadata.json")
            if merged_file_path:
                print(f"   • File tổng VI: {os.path.basename(merged_file_path)}")
            print(f"\n⏱️  HIỆU SUẤT:")
            print(f"   • Tổng thời gian: {elapsed:.2f}s ({elapsed/60:.1f} phút)")
            print(f"   • Tốc độ xử lý: {total_segments/(elapsed/60):.1f} câu/phút")
            print(f"\n💡 LƯU Ý:")
            print(f"   • Các file MP3 đã được tăng tốc (nếu cần)")
            print(f"   • Metadata chứa thông tin thời gian chính xác")
            print(f"   • Sẵn sàng để ghép vào file audio tổng")
            print(f"{'='*70}\n")

            Logger.success(f"Whisper hoàn tất: {len(tts_files_list)} TTS files sẵn sàng", elapsed)

            return {
                "status": "success",
                "engine": "faster-whisper",
                "total_segments": total_segments,
                "filtered_segments": filtered_count,
                "split_count": len(output_files_list),
                "output_files": output_files_list,
                "translated_files": translated_files_list,
                "merged_file": merged_file_path,
                "tts_files": tts_files_list,
                "tts_files_count": len(tts_files_list),
                "tts_ready_for_merge": True,
                "metadata_file": os.path.join(os.path.dirname(path), "tts", "timing_metadata.json"),
                "processing_time": elapsed,
                "speed_segments_per_minute": round(total_segments/(elapsed/60), 1),
                "statistics": {
                    "duplicates": stats['duplicates'],
                    "empty": stats['empty_segments'],
                    "short": stats['short_segments'],
                    "avg_length": round(stats['avg_segment_length'], 1),
                    "audio_duration": round(stats['total_duration'], 1),
                    "realtime_factor": round(stats['total_duration']/elapsed, 2)
                },
                "gemini_stats": {
                    "total_keys": len(GEMINI_API_KEYS) if GEMINI_API_KEYS else 0,
                    "failed_keys": len(GEMINI_STATE['failed_keys']),
                    "last_key_used": GEMINI_STATE['current_key_index'] + 1 if GEMINI_API_KEYS else 0
                }
            }
        else:
            raise HTTPException(400, "Chế độ này chỉ hỗ trợ faster-whisper")

    except HTTPException:
        raise
    except Exception as e:
        Logger.error("Lỗi Whisper", e)
        raise HTTPException(500, str(e))