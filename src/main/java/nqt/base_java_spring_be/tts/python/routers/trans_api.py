import os
import time
import pysrt
import threading
from fastapi import APIRouter, HTTPException
from schemas import TranslateRequest
from google.genai.types import GenerateContentConfig
from google import genai

from ai_core import AI_MODELS
from config import (
    TRANS_BATCH_SIZE, TRANS_DELAY_SECONDS_GEMINI,
    SYSTEM_INSTRUCTION_TRANS_GEMINI, GEMINI_API_KEYS, GEMINI_MODEL
)
from utils import Logger

router = APIRouter()

# === GLOBAL STATE CHO KEY ROTATION ===
GEMINI_STATE = {
    'current_key_index': 0,
    'failed_keys': set(),
    'lock': threading.Lock()
}


def get_next_gemini_client():
    with GEMINI_STATE['lock']:
        if not GEMINI_API_KEYS:
            return None, -1

        start_index = GEMINI_STATE['current_key_index']
        attempts = 0
        total_keys = len(GEMINI_API_KEYS)

        while attempts < total_keys:
            current_index = (start_index + attempts) % total_keys

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
    with GEMINI_STATE['lock']:
        GEMINI_STATE['failed_keys'].add(key_index)
        print(f"      ❌ Key #{key_index+1} đã bị đánh dấu thất bại (quota/error)")


def rotate_to_next_key():
    with GEMINI_STATE['lock']:
        if not GEMINI_API_KEYS:
            return None, -1

        current = GEMINI_STATE['current_key_index']
        total_keys = len(GEMINI_API_KEYS)

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


def call_gemini_api(text_list, max_retries=3):
    if not GEMINI_API_KEYS:
        return None

    prompt_content = "Dịch danh sách các dòng thoại sau sang Tiếng Việt (giữ nguyên số lượng dòng):\n"
    for i, txt in enumerate(text_list):
        prompt_content += f"Line_{i}: {txt}\n"

    total_attempts = 0
    max_total_attempts = len(GEMINI_API_KEYS) * max_retries if GEMINI_API_KEYS else max_retries

    while total_attempts < max_total_attempts:
        current_client = AI_MODELS.get("gemini_client")
        current_key_index = GEMINI_STATE['current_key_index']

        try:
            if not current_client:
                current_client, current_key_index = get_next_gemini_client()
                if not current_client:
                    print(f"      ❌ Không còn key khả dụng")
                    return None
                AI_MODELS["gemini_client"] = current_client

            time.sleep(TRANS_DELAY_SECONDS_GEMINI)

            response = current_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt_content,
                config=GenerateContentConfig(
                    temperature=0.1,
                    system_instruction=SYSTEM_INSTRUCTION_TRANS_GEMINI
                )
            )

            if not hasattr(response, 'text'):
                total_attempts += 1
                print(f"      ⚠️  Response không có text - Retry {total_attempts}/{max_total_attempts}")
                time.sleep(1)
                continue

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
            total_attempts += 1
            error_msg = str(e).lower()

            if "429" in error_msg or "quota" in error_msg or "resource_exhausted" in error_msg:
                print(f"      ⚠️  Key #{current_key_index+1} hết quota (429) - Đánh dấu key fail")
                mark_key_as_failed(current_key_index)

                next_client, next_key_index = rotate_to_next_key()
                if next_client:
                    AI_MODELS["gemini_client"] = next_client
                    print(f"      🔄 Retry với key #{next_key_index+1} (lần thử {total_attempts}/{max_total_attempts})...")
                    continue
                else:
                    print(f"      ❌ TẤT CẢ KEY ĐỀU HẾT QUOTA sau {total_attempts} lần thử")
                    return None

            print(f"      ❌ Gemini API lỗi: {str(e)[:100]} - Retry {total_attempts}/{max_total_attempts}")
            if total_attempts >= max_total_attempts:
                print(f"      ❌ Đã hết số lần retry ({max_total_attempts})")
                return None
            time.sleep(2)

    print(f"      ❌ Đã thử {total_attempts} lần nhưng vẫn thất bại")
    return None


def translate_srt_file_simple(input_srt_path):
    translate_start = time.time()

    if not GEMINI_API_KEYS:
        print(f"   ⚠️  Bỏ qua dịch: Không có Gemini API key")
        return None

    try:
        print(f"\n{'='*70}")
        print(f"🌐 BẮT ĐẦU DỊCH FILE SANG TIẾNG VIỆT")
        print(f"{'='*70}")
        print(f"   📂 File gốc: {os.path.basename(input_srt_path)}")

        dir_name, base_name = os.path.split(input_srt_path)
        output_path = os.path.join(dir_name, f"{os.path.splitext(base_name)[0]}_vi.srt")

        try:
            subs = pysrt.open(input_srt_path)
        except Exception:
            subs = pysrt.open(input_srt_path, encoding='utf-8')

        total_subs = len(subs)
        print(f"   📚 Tổng số dòng thoại: {total_subs}")
        print(f"   📦 Kích thước lô: {TRANS_BATCH_SIZE} dòng/lô")
        print(f"   🔑 Số key khả dụng: {len(GEMINI_API_KEYS) - len(GEMINI_STATE['failed_keys'])}/{len(GEMINI_API_KEYS)}")
        print(f"   ⏱️  Thời gian bắt đầu: {time.strftime('%H:%M:%S')}\n")

        total_translated = 0
        total_failed = 0
        total_mismatched = 0
        has_errors = False
        error_log = []

        for i in range(0, total_subs, TRANS_BATCH_SIZE):
            batch_start = time.time()
            current_batch = subs[i: i + TRANS_BATCH_SIZE]
            batch_size = len(current_batch)

            print(f"   🔄 Đang dịch lô {(i // TRANS_BATCH_SIZE) + 1}/{(total_subs - 1) // TRANS_BATCH_SIZE + 1} "
                  f"(dòng {i + 1}-{min(i + TRANS_BATCH_SIZE, total_subs)})...")

            original_texts = [sub.text for sub in current_batch]
            translated_texts = call_gemini_api(original_texts, max_retries=2)

            if translated_texts is None:
                error_msg = "Không có response từ API sau khi retry"
                print(f"      ⚠️  LỖI API: {error_msg} - Giữ nguyên {batch_size} dòng gốc")
                total_failed += batch_size
                has_errors = True
                error_log.append({
                    'batch': (i // TRANS_BATCH_SIZE) + 1,
                    'lines': f"{i + 1}-{min(i + TRANS_BATCH_SIZE, total_subs)}",
                    'error': error_msg,
                    'timestamp': time.strftime('%H:%M:%S')
                })

            elif len(translated_texts) != batch_size:
                error_msg = f"Lệch số dòng: Nhận {len(translated_texts)}/{batch_size}"
                print(f"      ⚠️  {error_msg} - Retry lần 1...")

                time.sleep(1)
                retry_translated = call_gemini_api(original_texts, max_retries=1)

                if retry_translated and len(retry_translated) == batch_size:
                    for j, new_text in enumerate(retry_translated):
                        if i + j < total_subs:
                            subs[i + j].text = new_text
                    total_translated += batch_size
                    print(f"      ✓ Retry thành công: {batch_size} dòng trong {time.time() - batch_start:.2f}s")
                else:
                    print(f"      ❌ Retry thất bại - Giữ nguyên text gốc")
                    total_mismatched += batch_size
                    has_errors = True
                    error_log.append({
                        'batch': (i // TRANS_BATCH_SIZE) + 1,
                        'lines': f"{i + 1}-{min(i + TRANS_BATCH_SIZE, total_subs)}",
                        'error': f"{error_msg} (retry failed)",
                        'timestamp': time.strftime('%H:%M:%S')
                    })
            else:
                for j, new_text in enumerate(translated_texts):
                    if i + j < total_subs:
                        subs[i + j].text = new_text
                total_translated += batch_size
                print(f"      ✓ Hoàn thành: {batch_size} dòng trong {time.time() - batch_start:.2f}s")

            subs.save(output_path, encoding='utf-8')

        error_report_path = None
        if has_errors:
            error_path = output_path.replace('.srt', '_[ERROR].srt')
            error_report_path = output_path.replace('.srt', '_ERROR_LOG.txt')

            with open(error_report_path, 'w', encoding='utf-8') as f:
                f.write("=" * 70 + "\n")
                f.write("BÁO CÁO LỖI DỊCH FILE SRT\n")
                f.write("=" * 70 + "\n\n")
                f.write(f"📂 File gốc: {os.path.basename(input_srt_path)}\n")
                f.write(f"📝 File đầu ra: {os.path.basename(error_path)}\n")
                f.write(f"⏱️  Thời gian: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write("📊 THỐNG KÊ:\n")
                f.write(f"   • Tổng số dòng: {total_subs}\n")
                f.write(f"   • Dịch thành công: {total_translated}\n")
                f.write(f"   • Lỗi API: {total_failed}\n")
                f.write(f"   • Lệch dòng: {total_mismatched}\n")
                f.write(f"   • Tổng lỗi: {len(error_log)}\n\n")
                f.write("=" * 70 + "\n")
                f.write("CHI TIẾT CÁC LỖI\n")
                f.write("=" * 70 + "\n\n")
                for idx, err in enumerate(error_log, 1):
                    f.write(f"[{idx}] Batch {err['batch']} | Dòng {err['lines']} | {err['timestamp']}\n")
                    f.write(f"    Lỗi: {err['error']}\n\n")
                f.write("=" * 70 + "\n")
                f.write(f"🔑 THÔNG TIN KEY:\n")
                f.write(f"   • Tổng số key: {len(GEMINI_API_KEYS)}\n")
                f.write(f"   • Key đã fail: {len(GEMINI_STATE['failed_keys'])}\n")
                f.write(f"   • Key cuối dùng: #{GEMINI_STATE['current_key_index'] + 1}\n")
                f.write("=" * 70 + "\n")

            os.rename(output_path, error_path)
            output_path = error_path
            print(f"\n   📄 Đã tạo báo cáo lỗi: {os.path.basename(error_report_path)}")

        translate_elapsed = time.time() - translate_start

        print(f"\n{'='*70}")
        print(f"✅ DỊCH HOÀN TẤT")
        print(f"{'='*70}")
        print(f"   📝 File đầu ra: {os.path.basename(output_path)}")
        print(f"   📊 Tổng số dòng: {total_subs}")
        print(f"   ✓ Dịch thành công: {total_translated}")
        print(f"   ⚠️  Lỗi API: {total_failed}")
        print(f"   ⚠️  Lệch dòng: {total_mismatched}")
        print(f"   🔑 Key đã dùng: {GEMINI_STATE['current_key_index'] + 1}/{len(GEMINI_API_KEYS)}")
        print(f"   ⏱️  Thời gian dịch: {translate_elapsed:.2f}s ({translate_elapsed / 60:.1f} phút)")
        if total_translated > 0:
            print(f"   ⚡ Tốc độ: {total_translated / (translate_elapsed / 60):.1f} dòng/phút")
        if has_errors:
            print(f"   📄 Báo cáo lỗi: {os.path.basename(error_report_path)}")
        print(f"{'='*70}\n")

        return {
            "has_errors": has_errors,
            "output_path": output_path,
            "error_report_path": error_report_path,
            "total_subs": total_subs,
            "total_translated": total_translated,
            "total_failed": total_failed,
            "total_mismatched": total_mismatched,
            "elapsed": translate_elapsed,
        }

    except Exception as e:
        print(f"\n{'='*70}")
        print(f"❌ LỖI NGHIÊM TRỌNG TRONG QUÁ TRÌNH DỊCH")
        print(f"{'='*70}")
        print(f"   🔴 Lỗi: {str(e)}")
        print(f"   📂 File: {os.path.basename(input_srt_path)}")
        print(f"{'='*70}\n")
        raise


# ============================================================================
# API ENDPOINT
# ============================================================================

@router.post("/api/v1/dubbing/translate")
def api_translate_gemini(req: TranslateRequest):
    if not GEMINI_API_KEYS:
        raise HTTPException(400, "Chưa cấu hình Gemini API key")

    input_path = os.path.abspath(req.input_srt_path)

    if not os.path.exists(input_path):
        raise HTTPException(404, f"File không tồn tại: {input_path}")

    if not input_path.lower().endswith('.srt'):
        raise HTTPException(400, f"File phải có định dạng .srt: {input_path}")

    # Reset failed keys trước mỗi request
    with GEMINI_STATE['lock']:
        GEMINI_STATE['failed_keys'].clear()
        print(f"   🔑 Đã reset danh sách key - Sẵn sàng dùng {len(GEMINI_API_KEYS)} key")

    try:
        result = translate_srt_file_simple(input_path)

        return {
            "status": "success" if not result["has_errors"] else "partial",
            "input_file": os.path.basename(input_path),
            "output_file": os.path.basename(result["output_path"]),
            "output_path": result["output_path"],
            "error_report": result["error_report_path"],
            "statistics": {
                "total_lines": result["total_subs"],
                "translated": result["total_translated"],
                "failed": result["total_failed"],
                "mismatched": result["total_mismatched"],
            },
            "processing_time": round(result["elapsed"], 2),
            "speed_lines_per_minute": round(
                result["total_translated"] / (result["elapsed"] / 60), 1
            ) if result["total_translated"] > 0 else 0,
            "gemini_stats": {
                "total_keys": len(GEMINI_API_KEYS),
                "failed_keys": len(GEMINI_STATE['failed_keys']),
                "last_key_used": GEMINI_STATE['current_key_index'] + 1,
            },
        }

    except Exception as e:
        Logger.error("Lỗi Dịch Gemini", e)
        raise HTTPException(500, str(e))