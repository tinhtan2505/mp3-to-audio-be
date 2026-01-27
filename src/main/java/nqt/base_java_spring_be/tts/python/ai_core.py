import shutil
import os
import time
import re
import torch
import whisper
from faster_whisper import WhisperModel
from google import genai
from google.genai.types import GenerateContentConfig, GoogleSearch
from openai import OpenAI
from deep_translator import GoogleTranslator

# Import Config & Utils
from config import *
from utils import Logger

# 1.5. Kho chứa các Model AI (Global State)
AI_MODELS = {
    "whisper": None,
    "gemini_client": None,
    "ollama_client": None,
    "device": "cpu"
}

def check_system_requirements():
    Logger.section("BƯỚC 1: KIỂM TRA HỆ THỐNG")
    if shutil.which("ffmpeg"):
        Logger.success("FFmpeg đã cài đặt.")
    else:
        Logger.error("Chưa cài đặt FFmpeg! (Tính năng ghép video sẽ lỗi)")

    print("\n🔍 THÔNG TIN CUDA/GPU:")
    print(f"   • torch.__version__: {torch.__version__}")
    print(f"   • torch.cuda.is_available(): {torch.cuda.is_available()}")

    if torch.cuda.is_available():
        AI_MODELS["device"] = "cuda"
        Logger.success(f"✅ Phát hiện GPU: {torch.cuda.get_device_name(0)}")
    else:
        AI_MODELS["device"] = "cpu"
        Logger.warning("⚠️ Không phát hiện GPU - Hệ thống sẽ chạy chậm trên CPU")

def load_ai_models():
    Logger.section(f"BƯỚC 2: TẢI CÁC MODEL AI (CHẾ ĐỘ: {WHISPER_BACKEND.upper()})")

    # 1. Load Whisper
    print(f"\n⏳ Đang tải Whisper Model: {WHISPER_MODEL_SIZE}...")
    start = time.time()
    try:
        if WHISPER_BACKEND == "faster":
            cpu_count = os.cpu_count() or 4
            optimal_threads = max(cpu_count - 2, 4)
            device = "cuda" if torch.cuda.is_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"

            print(f"   🎮 Chế độ: {device.upper()} | Compute: {compute_type} | Threads: {optimal_threads}")
            AI_MODELS["whisper"] = WhisperModel(
                model_size_or_path=WHISPER_MODEL_SIZE,
                device=device,
                compute_type=compute_type,
                cpu_threads=optimal_threads,
                num_workers=2
            )
            Logger.success(f"Faster-Whisper đã tải xong", time.time() - start)
        else:
            AI_MODELS["whisper"] = whisper.load_model(WHISPER_MODEL_SIZE, device=AI_MODELS["device"])
            Logger.success(f"OpenAI-Whisper đã tải xong", time.time() - start)
    except Exception as e:
        Logger.error("Lỗi tải Whisper", e)

    # 2. Config Gemini - ĐÚNG CHO PHIÊN BẢN 1.60.0
    AI_MODELS["gemini_client"] = None

    if GEMINI_API_KEYS and len(GEMINI_API_KEYS) > 0:
        print(f"\n⏳ Đang khởi tạo Gemini v1.60.0 (Có {len(GEMINI_API_KEYS)} key dự phòng)...")

        for index, key in enumerate(GEMINI_API_KEYS):
            if not key or "AIza" not in key:
                continue

            try:
                masked_key = f"{key[:5]}...{key[-4:]}" if len(key) > 10 else "******"
                print(f"   🔑 [Thử Key #{index+1}] {masked_key}")

                client = genai.Client(api_key=key)

                AI_MODELS["gemini_client"] = client
                Logger.success(f"Gemini đã kết nối thành công với Key #{index+1}")

                break

            except Exception as e:
                Logger.warning(f"⚠️ Key #{index+1} thất bại: {str(e)[:100]}... -> Đang chuyển Key tiếp theo.")
                continue

    if not AI_MODELS["gemini_client"]:
        Logger.error("❌ TẤT CẢ GEMINI KEY ĐỀU LỖI HOẶC DANH SÁCH TRỐNG.")

# --- CÁC HÀM XỬ LÝ DỊCH THUẬT ---

def is_valid_translation(text):
    """Kiểm tra chất lượng bản dịch"""
    if re.search(r'[\u4e00-\u9fff]', text):
        return False, "Còn dính tiếng Trung"
    if re.search(r'\b(the|is|are|you|me|goods|too|looks|like|what|so|yes|no)\b', text, re.IGNORECASE):
        return False, "Còn dính tiếng Anh"
    return True, "Hợp lệ"

def call_gemini_api(text_list):
    """Gọi Gemini API v1.60.0 để dịch batch"""
    client = AI_MODELS["gemini_client"]
    if not client:
        print("   ❌ Gemini client chưa được khởi tạo")
        return None

    prompt_content = "Dịch danh sách các dòng thoại sau sang Tiếng Việt (giữ nguyên số lượng dòng):\n"
    for i, txt in enumerate(text_list):
        prompt_content += f"Line_{i}: {txt}\n"

    retries = 3
    for attempt in range(retries):
        try:
            time.sleep(TRANS_DELAY_SECONDS_GEMINI)

            # CÁCH GỌI ĐÚNG cho v1.60.0
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt_content,
                config=GenerateContentConfig(
                    temperature=0.1,
                    system_instruction=SYSTEM_INSTRUCTION_TRANS_GEMINI
                )
            )

            # Kiểm tra response có text không
            if not hasattr(response, 'text'):
                print(f"      [Gemini] Response không có text attribute")
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
            print(f"      [Gemini Cảnh báo] Lỗi API (Lần {attempt+1}): {e}")
            if attempt < retries - 1:
                time.sleep(10)
            else:
                print(f"      [Gemini] Đã thử {retries} lần, thất bại hoàn toàn")

    return None

def call_ollama_api(text_list):
    """Gọi Ollama API để dịch batch"""
    client = AI_MODELS["ollama_client"]
    if not client:
        print("   ❌ Ollama client chưa được khởi tạo.")
        return None

    input_text_block = "\n".join([f"Line_{i}: {txt}" for i, txt in enumerate(text_list)])
    user_prompt = f"Dịch khối văn bản sau sang Tiếng Việt chuẩn Tiên Hiệp:\n\n{input_text_block}"

    retries = 3
    for attempt in range(retries):
        try:
            time.sleep(TRANS_DELAY_SECONDS_OLLAMA)
            response = client.chat.completions.create(
                model=OLLAMA_MODEL_NAME,
                messages=[
                    {"role": "system", "content": SYSTEM_INSTRUCTION_TRANS},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.1,
            )
            raw_text = response.choices[0].message.content.strip()
            translated_lines = []
            for line in raw_text.split('\n'):
                clean_line = line.strip()
                if ":" in clean_line and (clean_line.startswith("Line") or clean_line[0].isdigit()):
                    parts = clean_line.split(":", 1)
                    if len(parts) > 1:
                        translated_lines.append(parts[1].strip())
                elif clean_line:
                    translated_lines.append(clean_line)
            return translated_lines
        except Exception as e:
            print(f"      [Ollama Lỗi] Lần {attempt+1}: {e}")
            time.sleep(2)
    return None

def call_ollama_single_line(text, system_prompt):
    """Dịch 1 dòng bằng Ollama"""
    client = AI_MODELS["ollama_client"]
    try:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Dịch dòng này sang Tiếng Việt Tiên Hiệp (không dùng tiếng Anh/Trung): {text}"}
            ],
            temperature=0.3,
            presence_penalty=1.1
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return text

def process_batch_recursive(subs_slice, start_index):
    """Thuật toán chia để trị cho Gemini"""
    original_texts = [sub.text for sub in subs_slice]
    count = len(original_texts)
    if count == 0:
        return []

    translated_results = call_gemini_api(original_texts)
    if translated_results and len(translated_results) == count:
        return translated_results

    print(f"  [!!!] PHÁT HIỆN LỆCH DÒNG tại dòng {start_index + 1}. Chia nhỏ để xử lý lại...")
    if count == 1:
        print(f"  [Thất bại] Dòng {start_index + 1} AI bó tay. Giữ nguyên gốc.")
        return [f"[LỖI] {original_texts[0]}"]

    mid = count // 2
    part1 = process_batch_recursive(subs_slice[:mid], start_index)
    part2 = process_batch_recursive(subs_slice[mid:], start_index + mid)
    return part1 + part2

def process_batch_recursive_ollama(subs_slice, start_index):
    """Xử lý batch Ollama với retry"""
    original_texts = [sub.text for sub in subs_slice]
    count = len(original_texts)
    if count == 0:
        return []

    translated_results = call_ollama_api(original_texts)
    if not translated_results or len(translated_results) != count:
        translated_results = [None] * count

    final_results = []

    for i, (orig, trans) in enumerate(zip(original_texts, translated_results)):
        current_text = trans
        real_idx = start_index + i

        if current_text is None:
            current_text = call_ollama_single_line(orig, SYSTEM_INSTRUCTION_TRANS)

        MAX_RETRIES = 3
        attempt = 0
        while attempt < MAX_RETRIES:
            is_ok, reason = is_valid_translation(current_text)
            if is_ok:
                break
            attempt += 1
            print(f"      🔸 [Ollama Retry {attempt}] #{real_idx}: Lỗi '{reason}' -> Thử lại...")
            retry_prompt = f"{SYSTEM_INSTRUCTION_TRANS}\nLỗi trước đó: '{current_text}' ({reason}). Dịch lại:"
            current_text = call_ollama_single_line(orig, retry_prompt)

        if current_text and ":" in current_text:
            parts = current_text.split(":", 1)
            if len(parts) > 1:
                current_text = parts[1].strip()

        is_ok_final, reason_final = is_valid_translation(current_text)
        if is_ok_final:
            print(f"   🟢 [Ollama OK] #{real_idx}: {current_text}")
        else:
            print(f"   🔴 [Ollama Fail] #{real_idx}: {reason_final} -> Chờ Gemini")

        final_results.append(current_text)

    return final_results

def call_gemini_fix_lines(failed_map):
    """Sửa lỗi bằng Gemini v1.60.0 -> Fallback Google Translator"""
    if not failed_map:
        return {}

    client = AI_MODELS["gemini_client"]
    fallback_translator = GoogleTranslator(source='auto', target='vi')
    gemini_active = bool(client)

    print(f"\n🚑 [BƯỚC 2: CỨU HỘ] Xử lý {len(failed_map)} dòng lỗi...")

    CHUNK_SIZE = 20
    items = list(failed_map.items())
    fixed_results = {}

    for i in range(0, len(items), CHUNK_SIZE):
        chunk = items[i:i+CHUNK_SIZE]

        # --- GEMINI PHASE ---
        if gemini_active:
            try:
                prompt = "Bạn là Dịch giả Tiên Hiệp. Hãy dịch chính xác các dòng sau sang Tiếng Việt (giữ nguyên ID):\n"
                prompt += "\n".join([f"Line_{idx}: {txt}" for idx, txt in chunk])
                time.sleep(2)

                res = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config=GenerateContentConfig(temperature=0.1)
                )

                if hasattr(res, 'text'):
                    for line in res.text.strip().split('\n'):
                        match = re.match(r"Line_(\d+):\s*(.*)", line.strip())
                        if match:
                            idx_str, content = match.groups()
                            idx = int(idx_str)
                            fixed_results[idx] = content.strip()
                            print(f"      ✨ [Gemini Fix] #{idx}: {content.strip()}")

            except Exception as e:
                print(f"      ❌ GEMINI SẬP: {str(e)[:50]}... -> Chuyển Google")
                gemini_active = False

        # --- GOOGLE PHASE ---
        if not gemini_active:
            for idx, text in chunk:
                if idx not in fixed_results:
                    try:
                        translated = fallback_translator.translate(text)
                        fixed_results[idx] = translated
                        print(f"      🌍 [Google Fix] #{idx}: {translated}")
                        time.sleep(0.2)
                    except Exception:
                        fixed_results[idx] = text
                        print(f"      💀 [Google Fail] #{idx}: Giữ nguyên")

    return fixed_results