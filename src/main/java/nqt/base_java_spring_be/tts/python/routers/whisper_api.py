import os
import time
import pysrt
import threading
import uuid
import re
import asyncio
from fastapi import APIRouter, HTTPException
from schemas import WhisperRequest
from google.genai.types import GenerateContentConfig
from google import genai
from ai_core import AI_MODELS
from config import (
    WHISPER_BACKEND, MAX_SEGMENTS_PER_FILE, TRANS_BATCH_SIZE,
    TRANS_DELAY_SECONDS_GEMINI, SYSTEM_INSTRUCTION_TRANS_GEMINI,
    GEMINI_API_KEYS, VOICE_MALE, VOICE_FEMALE
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
# TTS FUNCTIONS - TẠO TỪNG FILE MP3 RIÊNG BIỆT THEO INDEX
# ============================================================================

async def generate_tts_internal(text, voice, output_file, rate="+0%"):
    """
    Hàm TTS nội bộ - wrapper cho generate_tts từ utils
    """
    try:
        await generate_tts(text, voice, output_file, rate)
        return True
    except Exception as e:
        print(f"      ❌ TTS error: {str(e)[:50]}")
        return False


async def tts_batch_for_vi_file(vi_srt_path, tts_files_list, lock):
    """
    Xử lý TTS cho file VI đã dịch xong
    TẠO TỪNG FILE MP3 RIÊNG BIỆT CHO MỖI SUBTITLE
    """
    start_time = time.time()

    try:
        # Tạo thư mục tts
        srt_dir = os.path.dirname(vi_srt_path)
        tts_dir = os.path.join(srt_dir, "tts")
        os.makedirs(tts_dir, exist_ok=True)

        print(f"\n{'='*70}")
        print(f"🎤 BẮT ĐẦU TẠO TTS TỰ ĐỘNG CHO FILE VI")
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

        print(f"   • Tổng câu: {total_subs:,}")
        print(f"   • Batch size: {BATCH_SIZE} câu/lần")
        print(f"   • Số batch: {(total_subs + BATCH_SIZE - 1) // BATCH_SIZE}")
        print(f"   • Max concurrent: {MAX_CONCURRENT_TASKS}")
        print(f"   • Ước tính thời gian: ~{(total_subs / BATCH_SIZE * 0.8):.0f}s ({(total_subs / BATCH_SIZE * 0.8 / 60):.1f} phút)\n")

        processed_count = 0
        success_count = 0
        failed_count = 0

        # Xử lý từng batch
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

                # Tên file: sử dụng index thực tế từ SRT file
                output_filename = f"{sub.index}.mp3"
                output_path = os.path.join(tts_dir, output_filename)

                batch_data.append({
                    'index': sub.index,  # Sử dụng index thực tế từ SRT
                    'text': clean_txt,
                    'voice': voice,
                    'output_path': output_path
                })

            if not batch_data:
                continue

            # TTS song song với giới hạn concurrent
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

            async def generate_with_limit(item):
                async with semaphore:
                    try:
                        success = await generate_tts_internal(
                            item['text'],
                            item['voice'],
                            item['output_path'],
                            rate="+0%"
                        )
                        return item['index'], success
                    except Exception as e:
                        return item['index'], False

            # Tạo tasks
            tasks = [generate_with_limit(item) for item in batch_data]

            # Chờ tất cả hoàn thành với timeout
            batch_start_time = time.time()
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=60 * len(batch_data)
                )

                # Đếm thành công/thất bại
                for idx, success in results:
                    processed_count += 1
                    if success:
                        success_count += 1
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
            print(f"   ⏱️ TTS time: {batch_tts_time:.1f}s | Avg: {batch_tts_time/len(batch_data):.2f}s/câu")
            print(f"   ✓ Thành công: {success_count} | ❌ Thất bại: {failed_count} | 📊 Đã xử lý: {processed_count}/{total_subs}")

        elapsed = time.time() - start_time

        print(f"\n{'='*70}")
        print(f"✅ TTS HOÀN TẤT")
        print(f"{'='*70}")
        print(f"   📁 Thư mục TTS: {tts_dir}")
        print(f"   📊 Tổng câu xử lý: {processed_count:,}/{total_subs:,}")
        print(f"   ✓ Thành công: {success_count:,} file MP3")
        print(f"   ❌ Thất bại: {failed_count:,} file")
        print(f"   ⏱️  Thời gian: {elapsed:.1f}s ({elapsed/60:.1f} phút)")
        if processed_count > 0:
            print(f"   ⚡ Tốc độ: {processed_count/elapsed:.1f} file/giây")
        print(f"{'='*70}\n")

    except Exception as e:
        print(f"\n{'='*70}")
        print(f"❌ LỖI TTS CHO FILE VI")
        print(f"{'='*70}")
        print(f"   🔴 Lỗi: {str(e)}")
        print(f"   📂 File: {os.path.basename(vi_srt_path)}")
        print(f"{'='*70}\n")


def tts_file_background(vi_srt_path, tts_files_list, lock):
    """
    Wrapper để chạy async TTS trong thread riêng
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(tts_batch_for_vi_file(vi_srt_path, tts_files_list, lock))
        loop.close()
    except Exception as e:
        print(f"   ❌ [Background TTS] Lỗi: {e}\n")


def translate_file_background(file_path, translated_files_list, lock, tts_files_list, tts_lock):
    """
    Hàm chạy trong thread riêng để dịch file mà không block Whisper
    SAU KHI DỊCH THÀNH CÔNG -> TỰ ĐỘNG GỌI TTS
    """
    try:
        translated_file = translate_srt_file_simple(file_path)

        if translated_file:
            with lock:
                translated_files_list.append(translated_file)
            print(f"   ✅ [Background] Đã dịch xong: {os.path.basename(translated_file)}\n")

            # ========== TỰ ĐỘNG TẠO TTS SAU KHI DỊCH THÀNH CÔNG ==========
            print(f"   🎤 [Background] Bắt đầu tạo TTS cho: {os.path.basename(translated_file)}\n")
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
            tts_files_list = []  # THÊM DANH SÁCH TTS
            translation_lock = threading.Lock()
            tts_lock = threading.Lock()  # THÊM LOCK CHO TTS
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

                        # DỊCH FILE TRONG BACKGROUND (sẽ tự động trigger TTS)
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

                    # DỊCH FILE CUỐI CÙNG (sẽ tự động trigger TTS)
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
            print(f"⏳ Đang chờ các tiến trình dịch và TTS hoàn thành...")
            print(f"{'='*70}\n")

            for i, thread in enumerate(translation_threads, 1):
                thread.join(timeout=600)  # Tăng timeout lên 10 phút cho cả dịch + TTS
                if thread.is_alive():
                    print(f"   ⚠️  Thread {i} vẫn đang chạy (timeout)")

            # Final report
            print(f"\n{'='*70}")
            print(f"✅ CHUYỂN ÂM THANH THÀNH VĂN BẢN HOÀN TẤT")
            print(f"{'='*70}")
            print(f"📊 THỐNG KÊ:")
            print(f"   • Ngôn ngữ: {info.language} (độ tin cậy: {info.language_probability:.2%})")
            print(f"   • Tổng số câu: {total_segments:,}")
            print(f"   • Câu hợp lệ: {total_segments:,}")
            print(f"   • Đã lọc bỏ:")
            print(f"      - Trùng lặp: {stats['duplicates']}")
            print(f"      - Rỗng: {stats['empty_segments']}")
            print(f"      - Quá ngắn: {stats['short_segments']}")
            print(f"      - Tổng cộng: {filtered_count}")
            print(f"   • Số file tạo ra: {len(output_files_list)}")
            print(f"   • Số file đã dịch: {len(translated_files_list)}")
            print(f"   • Số file TTS MP3: {len(tts_files_list)}")
            print(f"   • Độ dài trung bình: {stats['avg_segment_length']:.1f} ký tự/câu")
            print(f"\n⏱️  HIỆU SUẤT:")
            print(f"   • Tổng thời gian: {elapsed:.2f}s ({elapsed/60:.1f} phút)")
            print(f"   • Tốc độ xử lý: {total_segments/(elapsed/60):.1f} câu/phút")
            print(f"   • Thời lượng audio: ~{stats['total_duration']:.1f}s")
            print(f"   • Hệ số thời gian thực: {stats['total_duration']/elapsed:.2f}x")
            print(f"\n🔑 GEMINI KEY USAGE:")
            print(f"   • Key cuối cùng: #{GEMINI_STATE['current_key_index']+1}/{len(GEMINI_API_KEYS) if GEMINI_API_KEYS else 0}")
            print(f"   • Key đã fail: {len(GEMINI_STATE['failed_keys'])}")
            if GEMINI_STATE['failed_keys']:
                failed_list = ', '.join([f"#{i+1}" for i in sorted(GEMINI_STATE['failed_keys'])])
                print(f"   • Danh sách fail: {failed_list}")
            print(f"\n📁 CÁC FILE ĐẦU RA:")
            print(f"   === File gốc (Tiếng Trung) ===")
            for i, f in enumerate(output_files_list, 1):
                print(f"   {i}. {os.path.basename(f)}")
            if translated_files_list:
                print(f"\n   === File đã dịch (Tiếng Việt) ===")
                with translation_lock:
                    for i, f in enumerate(translated_files_list, 1):
                        print(f"   {i}. {os.path.basename(f)}")
            if tts_files_list:
                print(f"\n   === File TTS MP3 (Từng câu riêng biệt) ===")
                with tts_lock:
                    print(f"   📁 Thư mục: tts/")
                    print(f"   📊 Tổng số file: {len(tts_files_list)}")
                    if len(tts_files_list) <= 10:
                        for i, f in enumerate(tts_files_list, 1):
                            print(f"   {i}. {os.path.basename(f)}")
                    else:
                        for i in range(5):
                            print(f"   {i+1}. {os.path.basename(tts_files_list[i])}")
                        print(f"   ... ({len(tts_files_list) - 10} files khác)")
                        for i in range(-5, 0):
                            print(f"   {len(tts_files_list) + i + 1}. {os.path.basename(tts_files_list[i])}")
            print(f"{'='*70}\n")

            Logger.success(f"Whisper hoàn tất: {len(output_files_list)} files, {total_segments} câu", elapsed)

            return {
                "status": "success",
                "engine": "faster-whisper",
                "total_segments": total_segments,
                "filtered_segments": filtered_count,
                "split_count": len(output_files_list),
                "output_files": output_files_list,
                "translated_files": translated_files_list,
                "tts_files": tts_files_list,  # THÊM TTS FILES VÀO RESPONSE
                "tts_files_count": len(tts_files_list),
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