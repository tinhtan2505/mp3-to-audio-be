import os
import time
import uuid
import subprocess
import traceback
import sys
import shutil
import re
import warnings
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path

# --- THƯ VIỆN XỬ LÝ ÂM THANH & DỮ LIỆU ---
import librosa
import soundfile as sf
import numpy as np
import pysrt

# --- THƯ VIỆN AI & MODEL ---
import torch
import torchaudio
import whisper
from faster_whisper import WhisperModel
import edge_tts
from openai import OpenAI
import google.generativeai as genai
from google.api_core import exceptions
from deep_translator import GoogleTranslator

# --- FASTAPI & SERVER ---
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# --- TẮT CẢNH BÁO KHÔNG CẦN THIẾT ---
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# ==============================================================================
# PHẦN 1: CẤU HÌNH HỆ THỐNG (CONFIGURATION)
# ==============================================================================

# 1.1. Cấu hình Server
PORT = 8008  # Cổng mặc định

# 1.2. Cấu hình Whisper (Nhận dạng giọng nói)
# Tùy chọn: "faster" (Tối ưu CPU/GPU) | "openai" (Gốc)
WHISPER_BACKEND = "faster"
WHISPER_MODEL_SIZE = "large-v3"
MAX_SEGMENTS_PER_FILE = 300  # Số câu tối đa mỗi file SRT con

# 1.3. Cấu hình Dịch thuật (Gemini & Ollama)
GEMINI_API_KEY = "AIzaSyCXnrlISw4K86DwSR355LHJcuaiRHEd5Cs"  # ⚠️ Lưu ý: Nên bảo mật Key này
TRANS_BATCH_SIZE = 20           # Số dòng dịch mỗi lần gửi
TRANS_DELAY_SECONDS_GEMINI = 4  # Độ trễ tránh Rate Limit Gemini
TRANS_DELAY_SECONDS_OLLAMA = 1  # Độ trễ cho Ollama (nhẹ hơn)

# Ollama Config (Chạy Local)
OLLAMA_BASE_URL = 'http://localhost:11434/v1'
OLLAMA_API_KEY = 'ollama'       # Key giả lập cho client
OLLAMA_MODEL_NAME = "qwen2.5:7b" # Model đề xuất: qwen2.5:7b hoặc 14b

# 1.4. Cấu hình TTS (Chuyển văn bản thành giọng nói)
SAMPLE_RATE = 24000
DEFAULT_MUSIC_VOLUME = 0.4
DEFAULT_VOICE_VOLUME = 3.0
DEFAULT_DUCKING_RATIO = 5.0    # Tỷ lệ nén nhạc nền khi có giọng đọc
DEFAULT_ATTACK_TIME = 50       # Thời gian bắt đầu nén (ms)
DEFAULT_RELEASE_TIME = 300     # Thời gian nhả nén (ms)

VOICE_FEMALE = "vi-VN-HoaiMyNeural"
VOICE_MALE = "vi-VN-NamMinhNeural"

# 1.5. Kho chứa các Model AI (Global State)
AI_MODELS = {
    "whisper": None,
    "gemini_model": None,
    "ollama_client": None,
    "device": "cpu"
}

# 1.6. System Prompts (Hướng dẫn cho AI)
SYSTEM_INSTRUCTION_TRANS_GEMINI = """
# VAI TRÒ:
Bạn là "Cỗ máy chuyển ngữ phụ đề SRT Chính xác". Nhiệm vụ duy nhất của bạn là chuyển đổi dữ liệu ngôn ngữ từ Tiếng Trung sang Tiếng Việt.

# ĐỐI TƯỢNG XỬ LÝ:
Dòng phim: Tiên hiệp / Cổ trang / Xuyên không.

# KỶ LUẬT SẮT (BẮT BUỘC TUÂN THỦ 100%):
1. CƠ CHẾ KHÓA DỮ LIỆU:
   - Chỉ dịch văn bản. KHÔNG tự động điền tiếp cốt truyện.
   - Giữ nguyên ý nghĩa nhưng chuyển sang văn phong Tiên hiệp.

2. CẤU TRÚC 1:1 (QUAN TRỌNG NHẤT):
   - Input có bao nhiêu dòng, Output phải có chính xác bấy nhiêu dòng.
   - Tuyệt đối KHÔNG gộp dòng, KHÔNG tách dòng.
   - Trả về kết quả là danh sách các dòng đã dịch, ngăn cách bởi xuống dòng.

3. PHONG CÁCH DỊCH THUẬT (CỔ TRANG):
   - Đại từ: Ta, Đệ, Huynh, Muội, Sư phụ, Đồ nhi, Nàng, Chàng, Các hạ, Tại hạ... (Linh hoạt theo ngữ cảnh).
   - KHÔNG dùng: Anh/Em/Cậu/Tớ (trừ khi nhân vật độc thoại nội tâm về hiện đại).
   - Từ ngữ: Dùng Hán Việt cho thuật ngữ tu tiên (Thôn phệ, Linh lực, Thể chất, Bái kiến...).
   - Văn phong: Ngắn gọn, súc tích (Lip-sync).
"""

SYSTEM_INSTRUCTION_TRANS = """
Bạn là một Dịch Giả Tiên Hiệp/Huyền Huyễn lão luyện (như Lão Bản, Vong Ngữ).
Nhiệm vụ: Dịch phụ đề phim từ Tiếng Trung sang Tiếng Việt.

### 1. QUY TẮC CỐT LÕI (BẮT BUỘC):
- **THOÁT Ý:** Không dịch word-by-word. Phải dịch theo ngữ cảnh, sắp xếp lại câu từ cho thuần Việt.
- **VĂN PHONG:** Cổ trang, kiếm hiệp, câu từ ngắn gọn, đanh thép (để lồng tiếng).
- **CẤU TRÚC:** Giữ nguyên số lượng dòng và định dạng `Line_x: [Nội dung]`.

### 2. CẤU TRÚC CÂU (QUAN TRỌNG):
- Câu hỏi tu từ: "这不正是...吗" -> Dịch: **"Chẳng phải là... sao?"** (Hay hơn "Đây không phải là...").
- Câu cảm thán: Dùng từ đệm: **"Chậc"**, **"Hừ"**, **"Sao?"**.

### 3. QUY TẮC CẤM KỴ (VI PHẠM LÀ HỎNG):
- **CẤM TIẾNG ANH:** Tuyệt đối KHÔNG xuất hiện từ tiếng Anh (như: Too good, Goods, Looks like...).
- **CẤM TIẾNG TRUNG:** Nếu không dịch được, hãy phiên âm Hán Việt.
- **Xưng hô:** Ta - Ngươi, Sư phụ - Đồ nhi, Tỷ tỷ - Muội muội.

### 4. VÍ DỤ SỬA LỖI:
Input:
Line_0: Looks like 我捡到宝贝了
Line_1: 你的体质是Goods
Line_2: 练到一定程度

Output:
Line_0: Xem ra ta nhặt được bảo vật rồi.
Line_1: Thể chất của ngươi đúng là hàng hiếm.
Line_2: Luyện đến một trình độ nhất định.
"""

# ==============================================================================
# PHẦN 2: CÁC LỚP DTO (DATA TRANSFER OBJECTS)
# ==============================================================================
class WhisperRequest(BaseModel):
    input_path: str
    enable_diarization: bool = False

class TranslateRequest(BaseModel):
    input_srt_path: str

class TtsRequest(BaseModel):
    input_srt_path: str

class MixRequest(BaseModel):
    video_input: str
    instrumental: str
    voice_dub: str
    music_volume: float = None
    voice_volume: float = None
    ducking_ratio: float = None
    attack_time: int = None
    release_time: int = None
    remove_logo: bool = False
    logo_x: int = 20
    logo_y: int = 30
    logo_w: int = 250
    logo_h: int = 40
    branding_text: str = "NQT REVIEW"

# ==============================================================================
# PHẦN 3: TIỆN ÍCH HỖ TRỢ (UTILITIES & LOGGING)
# ==============================================================================
class Logger:
    """Class quản lý việc in log ra màn hình cho đẹp mắt và đồng bộ."""
    @staticmethod
    def info(msg):
        print(f"ℹ️  [THÔNG TIN] {msg}")

    @staticmethod
    def success(msg, elapsed=None):
        time_str = f" ({elapsed:.2f} giây)" if elapsed else ""
        print(f"✅ [THÀNH CÔNG] {msg}{time_str}")

    @staticmethod
    def warning(msg):
        print(f"⚠️  [CẢNH BÁO] {msg}")

    @staticmethod
    def error(msg, exc=None):
        print(f"❌ [LỖI] {msg}")
        if exc:
            print("🔻 CHI TIẾT LỖI (TRACEBACK):")
            traceback.print_exc()

    @staticmethod
    def section(title):
        print(f"\n{'='*60}")
        print(f"🚀 {title.upper()}")
        print(f"{'='*60}")

def get_timestamp_str():
    """Lấy chuỗi thời gian hiện tại để đặt tên file."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def format_timestamp(seconds: float):
    """Chuyển đổi giây sang định dạng SRT (HH:MM:SS,ms)."""
    whole_seconds = int(seconds)
    milliseconds = int((seconds - whole_seconds) * 1000)
    hours = whole_seconds // 3600
    minutes = (whole_seconds % 3600) // 60
    seconds = whole_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def write_srt_faster(segments, file_path, start_index=1):
    """
    Ghi danh sách segments ra file SRT.
    Hỗ trợ start_index để nối tiếp số thứ tự khi chia nhỏ file.
    """
    with open(file_path, "w", encoding="utf-8") as f:
        for i, segment in enumerate(segments, start=start_index):
            start_str = format_timestamp(segment.start)
            end_str = format_timestamp(segment.end)
            text = segment.text.strip()
            f.write(f"{i}\n{start_str} --> {end_str}\n{text}\n\n")

def normalize_segment_time(segment, min_duration=0.15):
    """Chuẩn hóa thời gian dựa trên word-timestamps để chính xác hơn."""
    if hasattr(segment, "words") and segment.words:
        start = segment.words[0].start
        end = segment.words[-1].end

        # Đảm bảo đoạn không quá ngắn
        if end - start < min_duration:
            end = start + min_duration

        segment.start = round(start, 3)
        segment.end = round(end, 3)
    return segment

def free_port_windows(port):
    """Tự động tìm và tắt tiến trình đang chiếm dụng cổng (Chỉ Windows)."""
    print(f"\n🧹 [AUTO-KILL] Đang kiểm tra cổng {port}...")
    try:
        # Tìm PID: netstat -ano | findstr :8008
        result = subprocess.run(f"netstat -ano | findstr :{port}", shell=True, capture_output=True, text=True)
        output = result.stdout.strip()

        if not output:
            print(f"   ✅ Cổng {port} đang rảnh. Tiếp tục...")
            return

        pids = set()
        for line in output.split('\n'):
            if "LISTENING" in line:
                parts = line.split()
                pid = parts[-1]
                pids.add(pid)

        if not pids:
            print(f"   ✅ Không tìm thấy tiến trình LISTENING nào.")
            return

        for pid in pids:
            if pid != "0": # 0 là System Idle
                print(f"   🔪 Đang tắt tiến trình PID {pid} để giải phóng cổng...")
                subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
                print(f"   ✅ Đã tắt PID {pid}.")
        time.sleep(1)

    except Exception as e:
        print(f"⚠️ Không thể tự động giải phóng cổng: {e}")
        print("   -> Vui lòng tắt thủ công nếu gặp lỗi.")

async def generate_tts(text, voice, output_file, rate="+0%"):
    """Gọi Edge-TTS để tạo file âm thanh từ văn bản."""
    # rate format: "+0%", "+10%", "-5%"
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_file)

# ==============================================================================
# PHẦN 4: LOGIC XỬ LÝ AI & DỊCH THUẬT (AI PROCESSING)
# ==============================================================================

# --- 4.1. Hỗ trợ Dịch thuật (API Calls) ---
def is_valid_translation(text):
    """Kiểm tra xem bản dịch có đạt chất lượng cơ bản không."""
    # Kiểm tra còn tiếng Trung
    if re.search(r'[\u4e00-\u9fff]', text):
        return False, "Còn dính tiếng Trung"
    # Kiểm tra tiếng Anh (các từ thông dụng)
    if re.search(r'\b(the|is|are|you|me|goods|too|looks|like|what|so|yes|no)\b', text, re.IGNORECASE):
        return False, "Còn dính tiếng Anh"
    return True, "Hợp lệ"

def call_gemini_api(text_list):
    """Gửi yêu cầu dịch danh sách dòng tới Gemini."""
    if not AI_MODELS["gemini_model"]: return None

    prompt_content = "Dịch danh sách các dòng thoại sau (giữ nguyên số lượng dòng):\n"
    for i, txt in enumerate(text_list):
        prompt_content += f"Line_{i}: {txt}\n"

    retries = 3
    for attempt in range(retries):
        try:
            time.sleep(TRANS_DELAY_SECONDS_GEMINI)
            response = AI_MODELS["gemini_model"].generate_content(
                prompt_content,
                generation_config=genai.types.GenerationConfig(temperature=0.1)
            )
            raw_text = response.text.strip()
            translated_lines = []

            # Phân tích kết quả trả về
            for line in raw_text.split('\n'):
                clean_line = line.strip()
                if ":" in clean_line and (clean_line.startswith("Line") or clean_line[0].isdigit()):
                    clean_line = clean_line.split(":", 1)[1].strip()
                elif len(clean_line) > 2 and clean_line[0].isdigit() and clean_line[1] in ['.', ')']:
                    clean_line = clean_line.split(' ', 1)[1].strip()
                if clean_line: translated_lines.append(clean_line)
            return translated_lines
        except Exception as e:
            print(f"      [Gemini Cảnh báo] Lỗi API (Lần {attempt+1}): {e}")
            time.sleep(10)
    return None

def call_ollama_api(text_list):
    """Gửi yêu cầu dịch tới Ollama (Local LLM)."""
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
                    if len(parts) > 1: translated_lines.append(parts[1].strip())
                elif clean_line:
                    translated_lines.append(clean_line)
            return translated_lines
        except Exception as e:
            print(f"      [Ollama Lỗi] Lần {attempt+1}: {e}")
            time.sleep(2)
    return None

def call_ollama_single_line(text, system_prompt):
    """Dịch lại 1 dòng duy nhất bằng Ollama (Dùng cho Retry)."""
    client = AI_MODELS["ollama_client"]
    try:
        response = client.chat.completions.create(
            model=OLLAMA_MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Dịch dòng này sang Tiếng Việt Tiên Hiệp (Tuyệt đối không dùng tiếng Anh/Trung): {text}"}
            ],
            temperature=0.3,
            presence_penalty=1.1
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return text

def call_gemini_fix_lines(failed_map):
    """
    Sửa lỗi bằng Gemini -> Fallback Google DeepTranslator
    Có LOG CHI TIẾT
    """
    if not failed_map: return {}
    fallback_translator = GoogleTranslator(source='auto', target='vi')
    gemini_active = bool(AI_MODELS["gemini_model"])

    print(f"\n🚑 [BƯỚC 2: CỨU HỘ] Xử lý {len(failed_map)} dòng lỗi...")

    CHUNK_SIZE = 20
    items = list(failed_map.items())
    fixed_results = {}

    for i in range(0, len(items), CHUNK_SIZE):
        chunk = items[i:i+CHUNK_SIZE]

        # --- GEMINI PHASE ---
        if gemini_active:
            try:
                prompt = "Bạn là Dịch giả Tiên Hiệp. Hãy dịch chính xác các dòng sau sang Tiếng Việt (giữ nguyên ID):\n" + "\n".join([f"Line_{idx}: {txt}" for idx, txt in chunk])
                time.sleep(2)
                res = AI_MODELS["gemini_model"].generate_content(prompt, generation_config=genai.types.GenerationConfig(temperature=0.1))

                for line in res.text.strip().split('\n'):
                    match = re.match(r"Line_(\d+):\s*(.*)", line.strip())
                    if match:
                        idx_str, content = match.groups()
                        idx = int(idx_str)
                        fixed_results[idx] = content.strip()
                        # LOG CHI TIẾT GEMINI
                        print(f"      ✨ [Gemini Fix] #{idx}: {content.strip()}")

            except Exception as e:
                print(f"      ❌ GEMINI SẬP: {str(e)[:50]}... -> Chuyển Google")
                gemini_active = False

        # --- GOOGLE PHASE (Khi Gemini sập hoặc lỗi) ---
        if not gemini_active:
            for idx, text in chunk:
                if idx not in fixed_results: # Chỉ dịch nếu Gemini chưa xong
                    try:
                        translated = fallback_translator.translate(text)
                        fixed_results[idx] = translated
                        print(f"      🌍 [Google Fix] #{idx}: {translated}")
                        time.sleep(0.2)
                    except Exception:
                        fixed_results[idx] = text # Cùng đường
                        print(f"      💀 [Google Fail] #{idx}: Giữ nguyên")

    return fixed_results

# --- 4.2. Logic Đệ quy Xử lý Batch ---
def process_batch_recursive(subs_slice, start_index):
    """Thuật toán 'Chia để trị' cho Gemini: Nếu batch lỗi, chia đôi để xử lý lại."""
    original_texts = [sub.text for sub in subs_slice]
    count = len(original_texts)
    if count == 0: return []

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

def process_batch_recursive_gemini(subs_slice, start_index):
    # Logic cũ cho Gemini thuần, giữ lại nếu cần
    texts = [s.text for s in subs_slice]
    count = len(texts)
    if count == 0: return []
    res = call_gemini_api(texts)
    if res and len(res) == count: return res
    if count == 1: return [f"[LỖI] {texts[0]}"]
    mid = count // 2
    return process_batch_recursive_gemini(subs_slice[:mid], start_index) + process_batch_recursive_gemini(subs_slice[mid:], start_index + mid)

def process_batch_recursive_ollama(subs_slice, start_index):
    """
    Xử lý Batch Ollama với LOG CHI TIẾT
    """
    original_texts = [sub.text for sub in subs_slice]
    count = len(original_texts)
    if count == 0: return []

    # 1. Gọi Batch
    translated_results = call_ollama_api(original_texts)
    if not translated_results or len(translated_results) != count:
        translated_results = [None] * count

    final_results = []

    # 2. Duyệt từng dòng để in Log & Retry
    for i, (orig, trans) in enumerate(zip(original_texts, translated_results)):
        current_text = trans
        real_idx = start_index + i

        # Nếu batch null, dịch lẻ
        if current_text is None:
            current_text = call_ollama_single_line(orig, SYSTEM_INSTRUCTION_TRANS)

        # Retry Loop
        MAX_RETRIES = 3
        attempt = 0
        while attempt < MAX_RETRIES:
            is_ok, reason = is_valid_translation(current_text)
            if is_ok: break
            attempt += 1
            # Log Retry
            print(f"      🔸 [Ollama Retry {attempt}] #{real_idx}: Lỗi '{reason}' -> Thử lại...")
            retry_prompt = f"{SYSTEM_INSTRUCTION_TRANS}\nLỗi trước đó: '{current_text}' ({reason}). Dịch lại:"
            current_text = call_ollama_single_line(orig, retry_prompt)

        # Clean format
        if current_text and ":" in current_text:
            parts = current_text.split(":", 1)
            if len(parts) > 1: current_text = parts[1].strip()

        # LOG KẾT QUẢ OLLAMA
        is_ok_final, reason_final = is_valid_translation(current_text)
        if is_ok_final:
            print(f"   🟢 [Ollama OK] #{real_idx}: {current_text}")
        else:
            print(f"   🔴 [Ollama Fail] #{real_idx}: {reason_final} -> Chờ Gemini")

        final_results.append(current_text)

    return final_results

# --- 4.3. Quản lý Khởi động Model ---
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

    # 2. Config Gemini
    if GEMINI_API_KEY and "AIza" in GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            AI_MODELS["gemini_model"] = genai.GenerativeModel(
                model_name='models/gemini-2.5-flash',
                system_instruction=SYSTEM_INSTRUCTION_TRANS_GEMINI
            )
            Logger.success("Gemini API đã sẵn sàng")
        except Exception as e:
            Logger.warning(f"Lỗi cấu hình Gemini: {e}")
    else:
        Logger.warning("⚠️ Chưa có GEMINI_API_KEY hợp lệ.")

    # 3. Config Ollama
    print(f"\n⏳ Đang cấu hình Ollama Client ({OLLAMA_MODEL_NAME})...")
    try:
        client = OpenAI(base_url=OLLAMA_BASE_URL, api_key=OLLAMA_API_KEY)
        AI_MODELS["ollama_client"] = client
        try:
            client.models.list()
            Logger.success(f"Ollama đã kết nối tại {OLLAMA_BASE_URL}")
        except Exception:
            Logger.warning("Không thể kết nối Ollama. Hãy chắc chắn app đang chạy.")
    except Exception as e:
        Logger.error("Lỗi cấu hình Ollama", e)

# ==============================================================================
# PHẦN 5: API HANDLERS (CONTROLLERS)
# ==============================================================================

# Khởi tạo App
@asynccontextmanager
async def lifespan(app: FastAPI):
    check_system_requirements()
    load_ai_models()
    Logger.section("MÁY CHỦ SẴN SÀNG")
    print(f"📡 API đang chạy tại: http://0.0.0.0:{PORT}")
    print("="*60 + "\n")
    yield
    print("\n👋 Tạm biệt!")

app = FastAPI(lifespan=lifespan)

# --- 5.1. API WHISPER (Tách lời thoại) ---
@app.post("/api/v1/dubbing/whisper")
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

# --- 5.2. API DỊCH THUẬT (GEMINI) ---
@app.post("/api/v1/dubbing/translate-gemini")
def api_translate_gemini(req: TranslateRequest):
    start_time = time.time()
    Logger.section("DỊCH THUẬT GEMINI")
    print(f"   • Đầu vào: {req.input_srt_path}")

    if not AI_MODELS["gemini_model"]:
        raise HTTPException(500, "Gemini chưa được cấu hình Key!")

    try:
        input_path = os.path.abspath(req.input_srt_path)
        if not os.path.exists(input_path): raise FileNotFoundError(f"Không tìm thấy: {input_path}")

        dir_name, base_name = os.path.split(input_path)
        output_path = os.path.join(dir_name, f"{os.path.splitext(base_name)[0]}_vi_TienHiep.srt")

        try: subs = pysrt.open(input_path)
        except: subs = pysrt.open(input_path, encoding='utf-8')
        total_subs = len(subs)
        print(f"   📚 Tổng số dòng thoại: {total_subs}")

        for i in range(0, total_subs, TRANS_BATCH_SIZE):
            batch_start = time.time()
            current_batch = subs[i : i + TRANS_BATCH_SIZE]
            translated_texts = process_batch_recursive(current_batch, i)

            print(f"\n--- LÔ: {min(i + TRANS_BATCH_SIZE, total_subs)}/{total_subs} | ⏳ {time.time() - batch_start:.2f}s ---")
            for j, new_text in enumerate(translated_texts):
                if i + j >= total_subs: break
                sub_item = subs[i+j]
                print(f"#{sub_item.index}: {sub_item.text} -> {new_text}")
                sub_item.text = new_text

            print(f"   💾 Đang lưu tạm...")
            subs.save(output_path, encoding='utf-8')

        elapsed = time.time() - start_time
        Logger.success("DỊCH GEMINI HOÀN TẤT", elapsed)
        return {"status": "success", "output_file": output_path, "total_lines": total_subs}
    except Exception as e:
        Logger.error("Lỗi Dịch Gemini", e)
        raise HTTPException(500, str(e))

# --- 5.3. API DỊCH THUẬT (OLLAMA + GEMINI FIX) ---
@app.post("/api/v1/dubbing/translate")
def api_translate(req: TranslateRequest):
    # LOG THỜI GIAN BẮT ĐẦU
    start_time = time.time()
    start_str = datetime.now().strftime("%H:%M:%S")

    Logger.section("DỊCH THUẬT: OLLAMA -> GEMINI -> GOOGLE")
    print(f"⏰ Thời gian bắt đầu: {start_str}")
    print(f"   • File: {req.input_srt_path}")

    if not AI_MODELS["ollama_client"]: raise HTTPException(500, "Ollama chưa kết nối")

    try:
        path = os.path.abspath(req.input_srt_path)
        out_path = path.replace(".srt", "_vi_Final.srt")
        try: subs = pysrt.open(path)
        except: subs = pysrt.open(path, encoding='utf-8')

        total_subs = len(subs)
        failed_lines_map = {} # Map các dòng lỗi cần cứu

        print(f"   📚 Tổng số dòng: {total_subs}")

        # --- GIAI ĐOẠN 1: OLLAMA ---
        print("\n" + "-"*40)
        print("🏁 BƯỚC 1: DỊCH THÔ (OLLAMA)")
        print("-"*40)

        for i in range(0, total_subs, TRANS_BATCH_SIZE):
            batch_start = time.time()
            current_batch = subs[i : i + TRANS_BATCH_SIZE]

            # Gọi hàm xử lý và in log chi tiết bên trong
            translated_texts = process_batch_recursive_ollama(current_batch, i)

            # Cập nhật Text & Check Lỗi để gom lại
            for j, new_text in enumerate(translated_texts):
                idx = i + j
                sub_item = subs[idx]
                orig_cn = sub_item.text

                sub_item.text = new_text

                # Check lại lần nữa để đưa vào list Failed
                is_ok, _ = is_valid_translation(new_text)
                if not is_ok:
                    failed_lines_map[idx] = orig_cn
                    sub_item.text = f"[CHỜ FIX] {orig_cn}"

            # LOG THỜI GIAN BATCH
            batch_dur = time.time() - batch_start
            print(f"⏱️  [Batch {i}-{min(i+20, total_subs)}] Hoàn thành trong {batch_dur:.2f}s")

            # Lưu tạm
            subs.save(out_path, encoding='utf-8')

        # --- GIAI ĐOẠN 2: CỨU HỘ (GEMINI -> GOOGLE) ---
        if failed_lines_map:
            fixed_map = call_gemini_fix_lines(failed_lines_map)

            success_count = 0
            for idx, fixed_text in fixed_map.items():
                if idx < len(subs):
                    subs[idx].text = fixed_text
                    success_count += 1
            print(f"\n   ✅ Đã sửa: {success_count}/{len(failed_lines_map)} dòng lỗi.")
        else:
            print("\n✨ Tuyệt vời! Ollama không gặp lỗi nào.")

        subs.save(out_path, encoding='utf-8')

        # LOG TỔNG KẾT
        total_elapsed = time.time() - start_time
        print("\n" + "="*60)
        print(f"🎉 DỊCH HOÀN TẤT!")
        print(f"⏰ Bắt đầu: {start_str} | Kết thúc: {datetime.now().strftime('%H:%M:%S')}")
        print(f"⏱️  Tổng thời gian: {total_elapsed:.2f} giây")
        print(f"💾 File ra: {out_path}")
        print("="*60)

        return {"status": "success", "output_file": out_path, "elapsed": total_elapsed}

    except Exception as e:
        Logger.error("Lỗi Dịch", e)
        raise HTTPException(500, str(e))

# --- 5.4. API TTS (TẠO GIỌNG ĐỌC THÔNG MINH) ---
@app.post("/api/v1/dubbing/tts-gen")
async def api_tts(req: TtsRequest):
    start_time = time.time()
    try:
        path = os.path.abspath(req.input_srt_path)
        subs = pysrt.open(path)
        if not subs: raise ValueError("SRT rỗng")

        total_seconds = (subs[-1].end.ordinal / 1000.0) + 20.0
        final_audio = np.zeros(int(total_seconds * SAMPLE_RATE), dtype=np.float32)
        last_end_sample = 0
        SAFETY_GAP = 0.1
        MAX_SPEED_UP = 60

        Logger.section("TTS THÔNG MINH V2")
        print(f"   • Đầu vào: {os.path.basename(path)}")

        for i, sub in enumerate(subs):
            txt_raw = sub.text.strip()
            clean_txt = re.sub(r"^\[.*?\]", "", txt_raw).strip()
            if not clean_txt: continue

            is_male = "[NAM" in txt_raw.upper() or "[M]" in txt_raw.upper()
            voice = VOICE_MALE if is_male else VOICE_FEMALE
            voice_icon = "👦" if is_male else "👩"

            start_sec = sub.start.ordinal / 1000.0
            end_sec = sub.end.ordinal / 1000.0
            slot_duration = end_sec - start_sec

            # Tính toán Hard Limit
            if i < len(subs) - 1:
                next_start = subs[i+1].start.ordinal / 1000.0
                hard_limit = next_start - SAFETY_GAP
            else:
                hard_limit = end_sec + 5.0
            hard_limit = max(hard_limit, end_sec)

            actual_start_sample = max(int(start_sec * SAMPLE_RATE), last_end_sample)
            actual_start_sec = actual_start_sample / SAMPLE_RATE
            available_space = hard_limit - actual_start_sec

            tmp_file = f"temp_{uuid.uuid4().hex}.mp3"

            # --- Xử lý Tạo Audio ---
            status_log = ""
            try:
                # Bước 1: Tạo tốc độ thường
                await generate_tts(clean_txt, voice, tmp_file, rate="+0%")
                y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
                y_final, _ = librosa.effects.trim(y, top_db=30)
                dur_original = len(y_final) / SAMPLE_RATE

                # Bước 2: Kiểm tra độ dài
                if dur_original <= available_space:
                    if dur_original <= slot_duration:
                        status_log = "✨ VỪA KHÍT"
                    else:
                        status_log = f"👌 MƯỢN {(dur_original - slot_duration):.2f}s"
                else:
                    # Cần tăng tốc
                    if available_space < 0.5: available_space = 0.5
                    needed_ratio = (dur_original / available_space) - 1.0
                    needed_percent = min(int(needed_ratio * 100) + 5, MAX_SPEED_UP)
                    final_rate_str = f"+{needed_percent}%"

                    os.remove(tmp_file)
                    await generate_tts(clean_txt, voice, tmp_file, rate=final_rate_str)
                    y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
                    y_final, _ = librosa.effects.trim(y, top_db=30)
                    status_log = f"⚡ TĂNG TỐC {final_rate_str}"

                # Bước 3: Ghép vào Audio tổng
                end_sample = actual_start_sample + len(y_final)
                if end_sample > len(final_audio):
                    padding = np.zeros(end_sample - len(final_audio) + SAMPLE_RATE, dtype=np.float32)
                    final_audio = np.concatenate((final_audio, padding))

                final_audio[actual_start_sample:end_sample] += y_final
                last_end_sample = end_sample

                print(f"   [{i+1:03d}] {voice_icon} Text: {clean_txt[:40]}... | {status_log}")

            except Exception as e:
                print(f"❌ [Lỗi câu {i+1}] {e}")
            finally:
                if os.path.exists(tmp_file): os.remove(tmp_file)

        # Lưu file cuối
        final_valid_len = max(last_end_sample, int(subs[-1].end.ordinal/1000 * SAMPLE_RATE))
        final_audio = final_audio[:final_valid_len + int(0.5*SAMPLE_RATE)]
        out_name = f"{path.replace('.srt', '')}_audio_{get_timestamp_str()}.wav"
        sf.write(out_name, final_audio, SAMPLE_RATE)

        elapsed = time.time() - start_time
        Logger.success(f"TTS Hoàn tất: {out_name}", elapsed)
        return {"status": "success", "output_file": out_name}

    except Exception as e:
        Logger.error("Lỗi TTS", e)
        raise HTTPException(500, str(e))

# --- 5.5. API MIX VIDEO (GHÉP PHIM) ---
@app.post("/api/v1/dubbing/mix-video")
def api_mix(req: MixRequest):
    start_time = time.time()
    Logger.section("GHÉP VIDEO (FFMPEG)")

    try:
        vid, inst, voice = req.video_input, req.instrumental, req.voice_dub

        # Kiểm tra file
        if not os.path.exists(vid): raise FileNotFoundError(f"Thiếu Video: {vid}")
        if not os.path.exists(voice): raise FileNotFoundError(f"Thiếu Voice: {voice}")

        m_vol = req.music_volume if req.music_volume is not None else DEFAULT_MUSIC_VOLUME
        has_music = (m_vol > 0) and os.path.exists(inst)

        video_dir = os.path.dirname(vid)
        out_file = os.path.join(video_dir, f"out_vi_{get_timestamp_str()}.mp4")

        # Cấu hình Audio Filter
        audio_filter = ""
        inputs = []

        if has_music:
            print(f"   🎚️  Chế độ: MIXING (Giọng + Nhạc nền)")
            duck, atk, rel = req.ducking_ratio or 5.0, req.attack_time or 50, req.release_time or 300
            inputs = ["-i", vid, "-i", inst, "-i", voice]
            # Input 0:Video, 1:Music, 2:Voice
            audio_filter = (
                f"[2:a]volume={req.voice_volume or 3.0},lowshelf=g=5:f=100:w=0.5[voice];"
                f"[voice]asplit[v_trig][v_mix];"
                f"[1:a]volume={m_vol}[bg];"
                f"[bg][v_trig]sidechaincompress=threshold=0.1:ratio={duck}:attack={atk}:release={rel}[bg_duck];"
                f"[bg_duck][v_mix]amix=inputs=2:duration=longest[a_out]"
            )
        else:
            print(f"   🎚️  Chế độ: VOICE ONLY (Chỉ giọng đọc)")
            inputs = ["-i", vid, "-i", voice]
            # Input 0:Video, 1:Voice
            audio_filter = f"[1:a]volume={req.voice_volume or 3.0},lowshelf=g=5:f=100:w=0.5[a_out]"

        # Cấu hình Video Filter (Logo)
        video_filter = ""
        video_map = "0:v"
        video_codec = "copy"

        if req.remove_logo:
            print("   🛡️  Xóa Logo & Chèn Thương hiệu: BẬT")
            brand = req.branding_text
            font_file = "Arial" # Tìm font trong thư mục nếu có
            for f in os.listdir(video_dir):
                if f.lower().endswith(('.ttf', '.otf')):
                    font_file = Path(video_dir).joinpath(f).as_posix().replace(":", "\\:")
                    break

            video_filter = (
                f"[0:v]delogo=x={req.logo_x}:y={req.logo_y}:w={req.logo_w}:h={req.logo_h}[v_cl];"
                f"[v_cl]drawtext=fontfile='{font_file}':text='{brand}':fontcolor=white:fontsize=24:"
                f"box=1:boxcolor=black@0.6:boxborderw=5:x={req.logo_x}+(({req.logo_w}-text_w)/2):"
                f"y={req.logo_y}+(({req.logo_h}-text_h)/2)[v_branded];"
            )
            video_map = "[v_branded]"
            video_codec = "libx264"

        # Tổng hợp lệnh
        full_filter = (video_filter + audio_filter) if video_filter else audio_filter
        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", full_filter,
            "-map", video_map, "-map", "[a_out]",
            "-c:v", video_codec, "-c:a", "aac", "-b:a", "192k"
        ]
        if video_codec == "libx264": cmd.extend(["-preset", "medium", "-crf", "23"])
        cmd.append(out_file)

        print("   ⏳ Đang render FFmpeg...")
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE)

        Logger.success("XỬ LÝ THÀNH CÔNG!", time.time() - start_time)
        print(f"   👉 File đích: {out_file}")
        return {"status": "success", "output_file": out_file}

    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
        print("\n❌ LỖI FFMPEG:\n" + "\n".join(err_msg.splitlines()[-10:]))
        raise HTTPException(500, "Lỗi khi chạy FFmpeg")
    except Exception as e:
        Logger.error("Lỗi hệ thống", e)
        raise HTTPException(500, str(e))

# ==============================================================================
# PHẦN 6: MAIN ENTRY POINT
# ==============================================================================
if __name__ == "__main__":
    free_port_windows(PORT)
    print(f"🚀 KHỞI ĐỘNG SERVER TRÊN CỔNG {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)