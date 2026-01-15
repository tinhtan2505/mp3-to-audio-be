import os
import time
import uuid
import subprocess
import traceback
import sys
import shutil
import re
from datetime import datetime
from contextlib import asynccontextmanager

# --- LIBROSA & AUDIO LIBS ---
import librosa
import soundfile as sf
import numpy as np
import pysrt

# --- AI LIBS ---
import torch
import torchaudio
import whisper
from whisper.utils import get_writer
from faster_whisper import WhisperModel
import edge_tts
from pathlib import Path

# --- FASTAPI ---
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# --- WARNINGS SUPPRESSION ---
import warnings
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# ============================================
# 1. CẤU HÌNH HỆ THỐNG (CONFIG)
# ============================================
PORT = 8008  # 🔥 Cổng cố định

# 🔥🔥🔥 CẤU HÌNH QUAN TRỌNG: CHỌN ENGINE WHISPER 🔥🔥🔥
# Options: "faster" (Khuyên dùng cho CPU) | "openai" (Gốc)
WHISPER_BACKEND = "faster"
WHISPER_MODEL_SIZE = "large-v3"
MAX_SEGMENTS_PER_FILE = 500

# Audio Config
SAMPLE_RATE = 24000
DEFAULT_MUSIC_VOLUME = 0.4
DEFAULT_VOICE_VOLUME = 3.0
DEFAULT_DUCKING_RATIO = 5.0
DEFAULT_ATTACK_TIME = 50
DEFAULT_RELEASE_TIME = 300

# Voice Config
VOICE_FEMALE = "vi-VN-HoaiMyNeural"
VOICE_MALE = "vi-VN-NamMinhNeural"

AI_MODELS = {
    "whisper": None,
    "device": "cpu"
}

# ============================================
# 2. LOGGER & HELPER TIỆN ÍCH
# ============================================
class Logger:
    @staticmethod
    def info(msg):
        print(f"ℹ️  [INFO] {msg}")

    @staticmethod
    def success(msg, elapsed=None):
        time_str = f" ({elapsed:.2f}s)" if elapsed else ""
        print(f"✅ [SUCCESS] {msg}{time_str}")

    @staticmethod
    def warning(msg):
        print(f"⚠️  [WARNING] {msg}")

    @staticmethod
    def error(msg, exc=None):
        print(f"❌ [ERROR] {msg}")
        if exc:
            print("🔻 CHI TIẾT LỖI (TRACEBACK):")
            traceback.print_exc()

    @staticmethod
    def section(title):
        print(f"\n{'='*60}")
        print(f"🚀 {title.upper()}")
        print(f"{'='*60}")

def get_timestamp_str():
    return datetime.now().strftime("%Y%m%d_%H%M%S")

def format_timestamp(seconds: float):
    whole_seconds = int(seconds)
    milliseconds = int((seconds - whole_seconds) * 1000)
    hours = whole_seconds // 3600
    minutes = (whole_seconds % 3600) // 60
    seconds = whole_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

def write_srt_faster(segments, file_path, start_index=1):
    """
    Hàm ghi file SRT dành riêng cho Faster-Whisper.
    Hỗ trợ tham số start_index để nối số thứ tự giữa các file.
    """
    with open(file_path, "w", encoding="utf-8") as f:
        # Sử dụng start=start_index thay vì cố định là 1
        for i, segment in enumerate(segments, start=start_index):
            start_str = format_timestamp(segment.start)
            end_str = format_timestamp(segment.end)
            text = segment.text.strip()
            f.write(f"{i}\n{start_str} --> {end_str}\n{text}\n\n")

def normalize_segment_time(segment, min_duration=0.15):
    """
    Normalize timestamp bằng word timestamp + clamp an toàn
    """
    if hasattr(segment, "words") and segment.words:
        start = segment.words[0].start
        end = segment.words[-1].end

        # 🔒 Clamp chống đoạn quá ngắn (edge case)
        if end - start < min_duration:
            end = start + min_duration

        segment.start = round(start, 3)
        segment.end = round(end, 3)

    return segment


def free_port_windows(port):
    """
    🔥 HÀM MỚI: Tự động tìm và tắt tiến trình đang chiếm port
    """
    print(f"\n🧹 [AUTO-KILL] Đang kiểm tra cổng {port}...")
    try:
        # Tìm PID đang chiếm port: netstat -ano | findstr :8008
        result = subprocess.run(f"netstat -ano | findstr :{port}", shell=True, capture_output=True, text=True)
        output = result.stdout.strip()

        if not output:
            print(f"   ✅ Cổng {port} đang rảnh. Tiếp tục...")
            return

        # Parse lấy PID
        pids = set()
        lines = output.split('\n')
        for line in lines:
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

        # Đợi hệ điều hành giải phóng hoàn toàn
        time.sleep(1)

    except Exception as e:
        print(f"⚠️ Không thể tự động giải phóng port: {e}")
        print("   -> Bạn có thể cần tắt thủ công.")

# ============================================
# 3. QUY TRÌNH KHỞI ĐỘNG (STARTUP WORKFLOW)
# ============================================

def check_system_requirements():
    Logger.section("BƯỚC 1: KIỂM TRA HỆ THỐNG")

    if shutil.which("ffmpeg"):
        Logger.success("FFmpeg OK.")
    else:
        Logger.error("Chưa cài đặt FFmpeg! (Mix Video sẽ lỗi)")

    # 🔥 DEBUG CUDA CHI TIẾT
    print("\n🔍 CUDA DEBUG INFO:")
    print(f"   • torch.__version__: {torch.__version__}")
    print(f"   • torch.version.cuda: {torch.version.cuda}")
    print(f"   • torch.cuda.is_available(): {torch.cuda.is_available()}")

    if torch.cuda.is_available():
        print(f"   • torch.cuda.device_count(): {torch.cuda.device_count()}")
        print(f"   • torch.cuda.get_device_name(0): {torch.cuda.get_device_name(0)}")
        print(f"   • torch.cuda.current_device(): {torch.cuda.current_device()}")

        AI_MODELS["device"] = "cuda"
        Logger.success(f"✅ Phát hiện GPU: {torch.cuda.get_device_name(0)}")
    else:
        AI_MODELS["device"] = "cpu"
        Logger.warning("⚠️ Không phát hiện GPU - Chạy trên CPU")
        print("   💡 Kiểm tra:")
        print("      1. pip show torch → Có chữ '+cu' không?")
        print("      2. Đang dùng đúng Python trong .venv chưa?")
        print("      3. CUDA_VISIBLE_DEVICES=%CUDA_VISIBLE_DEVICES%")

def load_ai_models():
    Logger.section(f"BƯỚC 2: LOAD AI MODELS (MODE: {WHISPER_BACKEND.upper()})")

    print(f"\n⏳ Loading Whisper Model: {WHISPER_MODEL_SIZE}...")
    start = time.time()

    try:
        if WHISPER_BACKEND == "faster":
            # 🔥 CẤU HÌNH TỐI ƯU CHO FASTER-WHISPER

            # Xác định số thread tối ưu
            cpu_count = os.cpu_count() or 4
            optimal_threads = max(cpu_count - 2, 4)  # Để lại 2 core cho hệ thống

            # Kiểm tra GPU
            device = "cuda" if torch.cuda.is_available() else "cpu"

            if device == "cuda":
                # Nếu có GPU → dùng float16
                compute_type = "float16"
                print(f"   🎮 GPU Mode: {torch.cuda.get_device_name(0)}")
            else:
                # Nếu CPU → dùng int8
                compute_type = "int8"
                print(f"   💻 CPU Mode: {optimal_threads} threads")

            AI_MODELS["whisper"] = WhisperModel(
                model_size_or_path=WHISPER_MODEL_SIZE,
                device=device,
                compute_type=compute_type,
                cpu_threads=optimal_threads,
                num_workers=2  # Song song hóa preprocessing
            )
            Logger.success(f"Faster-Whisper Loaded ({device.upper()}, {compute_type})", time.time() - start)

        elif WHISPER_BACKEND == "openai":
            # OpenAI Whisper
            AI_MODELS["whisper"] = whisper.load_model(
                WHISPER_MODEL_SIZE,
                device=AI_MODELS["device"]
            )
            Logger.success(f"OpenAI-Whisper Loaded ({AI_MODELS['device'].upper()})", time.time() - start)

    except Exception as e:
        Logger.error("Lỗi Load Whisper", e)

# ============================================
# 4. LIFESPAN
# ============================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    check_system_requirements()
    load_ai_models()

    Logger.section("SERVER SẴN SÀNG")
    print(f"📡 API đang chạy tại: http://0.0.0.0:{PORT}")
    print("="*60 + "\n")
    yield
    print("\n👋 Tạm biệt!")

app = FastAPI(lifespan=lifespan)

# ============================================
# 5. LOGIC & API
# ============================================

# 🔥 HÀM ĐƯỢC THÊM LẠI
async def generate_tts(text, voice, output_file, rate="+0%"):
    # rate string format: "+0%", "+10%", "-5%"
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_file)
# --- DTO ---
class WhisperRequest(BaseModel):
    input_path: str
    enable_diarization: bool = False

class TtsRequest(BaseModel): input_srt_path: str
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

# --- APIs ---
@app.post("/api/v1/dubbing/whisper")
def api_whisper(req: WhisperRequest):
    if not AI_MODELS["whisper"]:
        raise HTTPException(500, "Whisper chưa load")

    try:
        path = os.path.abspath(req.input_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"File không tồn tại: {path}")

        print("\n" + "=" * 60)
        print(f"🎤 WHISPER PROCESSING (TIMESTAMP-ACCURATE MODE)")
        print(f"   • Input: {os.path.basename(path)}")

        start_w = time.time()

        print(f"   • Start Time: {datetime.fromtimestamp(start_w).strftime('%H:%M:%S')}")

        # =====================================================
        # 🔥 FASTER-WHISPER – TIMESTAMP FIRST CONFIG
        # =====================================================
        if WHISPER_BACKEND == "faster":
            print("   ⏳ Transcribing (Faster-Whisper | Accurate Time)...")

            segments, info = AI_MODELS["whisper"].transcribe(
                path,
                language="zh",
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=500,   # Khoảng lặng >0.5s sẽ bị cắt (giúp tách câu tốt hơn)
                    speech_pad_ms=400              # Giữ lại một chút âm thanh quanh tiếng nói để không bị mất chữ đầu/cuối
                ),
                condition_on_previous_text=False,

                beam_size=1,            # ⚡ Nhanh nhất (Greedy)
                best_of=1,              # Đi kèm với beam_size=1
                temperature=0.0,        # Greedy cần nhiệt độ 0

                # beam_size=5,
                # best_of=5,
                # temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],

                repetition_penalty=1.2,
                no_speech_threshold=0.6,

                word_timestamps=True,  # ✅ BẮT BUỘC

                # ===============================
                # 🛡️ HẠN CHẾ HALLUCINATION NHẸ
                # ===============================
                compression_ratio_threshold=2.0,
                log_prob_threshold=-1.0,

                initial_prompt=None
            )

            # =====================================================
            # 🔥 FIX TIME BẰNG WORD TIMESTAMP
            # =====================================================
            segments_list = []
            for seg in segments:
                seg = normalize_segment_time(seg)  # 🔥 DÒNG QUAN TRỌNG NHẤT
                segments_list.append(seg)

            # =====================================================
            # 📊 DEBUG INFO
            # =====================================================
            elapsed = time.time() - start_w
            print(f"\n📊 THỐNG KÊ:")
            print(f"   • Language: {info.language} ({info.language_probability:.2%})")
            print(f"   • Tổng câu: {len(segments_list)}")
            print(f"   • Thời gian xử lý: {elapsed:.2f}s")

            # =====================================================
            # 💾 SAVE SRT
            # =====================================================
            out_dir = os.path.dirname(path)
            base_filename = os.path.splitext(os.path.basename(path))[0].split('_')[0]
            timestamp_str = get_timestamp_str()

            output_files_list = []

            # 1. Chia list to thành các chunks nhỏ
            # Ví dụ: list 1200 câu, max 500 -> [500, 500, 200]
            chunks = [segments_list[i:i + MAX_SEGMENTS_PER_FILE]
                      for i in range(0, len(segments_list), MAX_SEGMENTS_PER_FILE)]

            print(f"   ✂️  Chia thành {len(chunks)} phần (Max {MAX_SEGMENTS_PER_FILE} câu/file)...")

            current_srt_index = 1

            # 2. Lặp và ghi từng file
            for idx, chunk in enumerate(chunks):
                # Tạo tên file có số thứ tự: _part01, _part02...
                part_suffix = f"_part{idx+1:02d}"
                out_name = f"{base_filename}_cn_{timestamp_str}{part_suffix}.srt"
                full_path = os.path.join(out_dir, out_name)

                write_srt_faster(chunk, full_path, start_index=current_srt_index)
                output_files_list.append(full_path)
                print(f"      -> Đã ghi: {out_name} ({len(chunk)} câu) => 📂 {full_path}")
                current_srt_index += len(chunk)

            Logger.success(f"Whisper xong. Tổng {len(chunks)} files.", elapsed)

            return {
                "status": "success",
                "engine": "faster-whisper",
                "total_segments": len(segments_list),
                "split_count": len(chunks),
                "output_files": output_files_list  # Trả về danh sách file
            }

        # =====================================================
        # (OPTIONAL) OPENAI WHISPER – KHÔNG KHUYẾN NGHỊ
        # =====================================================
        else:
            raise HTTPException(400, "Chế độ openai-whisper không hỗ trợ timestamp chuẩn cho lồng tiếng")

    except Exception as e:
        Logger.error("Whisper Error", e)
        raise HTTPException(500, str(e))


@app.post("/api/v1/dubbing/tts-gen")
async def api_tts(req: TtsRequest):
    start_time = time.time()
    try:
        path = os.path.abspath(req.input_srt_path)
        subs = pysrt.open(path)
        if not subs: raise ValueError("SRT rỗng")

        # Tạo buffer tổng
        total_seconds = (subs[-1].end.ordinal / 1000.0) + 20.0
        final_audio = np.zeros(int(total_seconds * SAMPLE_RATE), dtype=np.float32)

        last_end_sample = 0
        SAFETY_GAP = 0.1  # Giữ khoảng cách 0.1s an toàn
        MAX_SPEED_UP = 60 # Chỉ cho phép tăng tốc tối đa +60% (để tránh giọng quá nhanh không nghe kịp)

        print("\n" + "="*90)
        print(f"🎙️  SMART TTS V2 (RE-GENERATION MODE)")
        print(f"   • Input: {os.path.basename(path)}")
        print(f"   • Start Time: {datetime.fromtimestamp(start_time).strftime('%H:%M:%S')}")
        print("="*90)

        for i, sub in enumerate(subs):
            # 1. Lọc text
            txt_raw = sub.text.strip()
            clean_txt = re.sub(r"^\[.*?\]", "", txt_raw).strip()
            if not clean_txt: continue

            # 2. Xác định Voice
            is_male = "[NAM" in txt_raw.upper() or "[M]" in txt_raw.upper()
            voice = VOICE_MALE if is_male else VOICE_FEMALE
            voice_icon = "👦" if is_male else "👩"

            # 3. Tính toán thời gian Slot & Hard Limit
            start_seconds = sub.start.ordinal / 1000.0
            end_seconds = sub.end.ordinal / 1000.0
            slot_duration = end_seconds - start_seconds

            # Logic Hard Limit (Mượn Gap)
            if i < len(subs) - 1:
                next_start = subs[i+1].start.ordinal / 1000.0
                hard_limit = next_start - SAFETY_GAP
            else:
                hard_limit = end_seconds + 5.0

            hard_limit = max(hard_limit, end_seconds)

            # Xác định điểm bắt đầu thực tế trên Timeline
            actual_start_sample = max(int(start_seconds * SAMPLE_RATE), last_end_sample)
            actual_start_seconds = actual_start_sample / SAMPLE_RATE

            # KHOẢNG TRỐNG TỐI ĐA (Available Space)
            available_space = hard_limit - actual_start_seconds

            tmp_file = f"temp_{uuid.uuid4().hex}.mp3"

            # Biến lưu thông tin xử lý để in log
            process_step = 1 # 1: Normal, 2: Re-gen
            final_rate_str = "+0%"
            status_icon = "✅"
            action_log = ""

            try:
                # --- BƯỚC 1: TẠO FILE TỐC ĐỘ GỐC (+0%) ---
                await generate_tts(clean_txt, voice, tmp_file, rate="+0%")

                # Kiểm tra độ dài
                y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
                y_trimmed, _ = librosa.effects.trim(y, top_db=30)
                dur_original = len(y_trimmed) / SAMPLE_RATE

                dur_final = dur_original # Mặc định là bằng gốc

                # --- BƯỚC 2: KIỂM TRA & QUYẾT ĐỊNH ---

                # Case A: Vừa khít slot hoặc vừa Gap -> Dùng luôn
                if dur_original <= available_space:
                    if dur_original <= slot_duration:
                        status_icon = "✨ PERFECT"
                        action_log = "Vừa khít slot gốc."
                    else:
                        status_icon = "👌 BORROW"
                        borrow = dur_original - slot_duration
                        action_log = f"Mượn {borrow:.2f}s khoảng lặng."

                    y_final = y_trimmed

                # Case B: Dài quá -> Cần tạo lại (RE-GEN)
                else:
                    process_step = 2

                    # Tính tỷ lệ cần tăng tốc
                    # Công thức: T_new = T_old / (1 + rate) => rate = (T_old / T_target) - 1
                    # T_target ở đây là available_space
                    if available_space < 0.5: available_space = 0.5 # Tránh chia cho 0 hoặc quá nhỏ

                    needed_ratio = (dur_original / available_space) - 1.0
                    needed_percent = int(needed_ratio * 100)

                    # Thêm 5% buffer an toàn (để đảm bảo chắc chắn vừa sau khi cắt silence)
                    needed_percent += 5

                    # Cắt trần (Max Speed Cap)
                    if needed_percent > MAX_SPEED_UP:
                        final_percent = MAX_SPEED_UP
                        status_icon = "🐢 LAG" # Vẫn trễ dù đã max tốc
                        extra_msg = "(Đã max tốc)"
                    else:
                        final_percent = needed_percent
                        status_icon = "⚡ RE-GEN"
                        extra_msg = ""

                    final_rate_str = f"+{final_percent}%"
                    action_log = f"Gốc dài {dur_original:.2f}s > Gap {available_space:.2f}s. Tạo lại với tốc độ {final_rate_str} {extra_msg}"

                    # 🔥 GỌI EDGE-TTS LẦN 2 VỚI TỐC ĐỘ MỚI
                    if os.path.exists(tmp_file): os.remove(tmp_file) # Xóa file cũ
                    await generate_tts(clean_txt, voice, tmp_file, rate=final_rate_str)

                    # Load file mới
                    y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
                    y_final, _ = librosa.effects.trim(y, top_db=30)
                    dur_final = len(y_final) / SAMPLE_RATE

                    # Check lại xem có bị trễ không (cho log thôi)
                    if dur_final > available_space:
                        diff = dur_final - available_space
                        action_log += f" -> Vẫn trễ {diff:.2f}s"

                # 7. Ghép vào Timeline
                end_sample = actual_start_sample + len(y_final)

                if end_sample > len(final_audio):
                    padding = np.zeros(end_sample - len(final_audio) + SAMPLE_RATE, dtype=np.float32)
                    final_audio = np.concatenate((final_audio, padding))

                final_audio[actual_start_sample:end_sample] += y_final
                last_end_sample = end_sample

                # 8. PRINT LOG CHI TIẾT
                display_text = (clean_txt[:45] + "...") if len(clean_txt) > 45 else clean_txt

                print(f"   [{i+1:03d}] {voice_icon} [{sub.start} -> {sub.end}]")
                print(f"         📝 Text : {display_text}")
                print(f"         ⏱️  Time : Slot: {slot_duration:.2f}s | Gap Max: {available_space:.2f}s")

                if process_step == 1:
                    print(f"         💿 Audio: {dur_original:.2f}s (Gốc) | {status_icon} {action_log}")
                else:
                    # Log kiểu so sánh 2 bước
                    print(f"         🔄 Xử lý: {dur_original:.2f}s (Gốc) -> Quá dài")
                    print(f"         🚀 ReGen: {dur_final:.2f}s (Rate {final_rate_str}) | {status_icon} {action_log}")

                print("   " + "-"*60)

            except Exception as e:
                print(f"❌ [LỖI CÂU {i+1}] {e}")
            finally:
                if os.path.exists(tmp_file): os.remove(tmp_file)

        # Cắt file đúng độ dài
        final_valid_len = max(last_end_sample, int(subs[-1].end.ordinal/1000 * SAMPLE_RATE))
        final_audio = final_audio[:final_valid_len + int(0.5*SAMPLE_RATE)]

        out_name = f"{path.replace('.srt', '')}_audio_{get_timestamp_str()}.wav"
        sf.write(out_name, final_audio, SAMPLE_RATE)

        elapsed = time.time() - start_time
        print("="*90)
        print(f"   ⏱️  Tổng thời gian xử lý: {elapsed:.2f}s")
        Logger.success(f"TTS Hoàn tất: {out_name}")
        return {"status": "success", "output_file": out_name}

    except Exception as e:
        Logger.error("TTS Error", e)
        traceback.print_exc()
        raise HTTPException(500, str(e))

@app.post("/api/v1/dubbing/mix-video")
def api_mix(req: MixRequest):
    start_time = time.time()

    print("\n" + "="*70)
    print(f"🎬 [START] MIX VIDEO PROCESSING | Time: {get_timestamp_str()}")
    print(f"   • Start Time: {datetime.fromtimestamp(start_time).strftime('%H:%M:%S')}")
    print("="*70)

    try:
        # =========================================================================
        # BƯỚC 1: KIỂM TRA INPUT & PATHS
        # =========================================================================
        print("\n🔹 BƯỚC 1: CHUẨN BỊ FILE INPUT")

        vid = req.video_input
        inst = req.instrumental
        voice = req.voice_dub

        # Lấy volume cấu hình
        v_vol = req.voice_volume or DEFAULT_VOICE_VOLUME
        m_vol = req.music_volume if req.music_volume is not None else DEFAULT_MUSIC_VOLUME

        # Kiểm tra file
        if not os.path.exists(vid): raise FileNotFoundError(f"Không thấy Video: {vid}")
        if not os.path.exists(voice): raise FileNotFoundError(f"Không thấy Voice: {voice}")

        # Chỉ kiểm tra file nhạc nếu m_vol > 0
        has_music = (m_vol > 0)
        if has_music and not os.path.exists(inst):
            print(f"   ⚠️ Không tìm thấy file nhạc '{inst}', tự động chuyển về chế độ KHÔNG NHẠC.")
            has_music = False

        video_dir = os.path.dirname(vid)
        out_filename = f"out_vi_{get_timestamp_str()}.mp4"
        out = os.path.join(video_dir, out_filename)

        print(f"   📂 [Video Gốc] : {os.path.basename(vid)}")
        print(f"   🗣️  [Giọng Đọc] : {os.path.basename(voice)}")
        if has_music:
            print(f"   🎵 [Nhạc Nền]  : {os.path.basename(inst)} (Vol: {m_vol})")
        else:
            print(f"   🔇 [Nhạc Nền]  : KHÔNG SỬ DỤNG (Vol = 0 hoặc file lỗi)")

        print(f"   💾 [File Đích] : {out_filename}")

        # =========================================================================
        # BƯỚC 2: CẤU HÌNH AUDIO FILTER (LOGIC MỚI 🔥)
        # =========================================================================
        print("\n🔹 BƯỚC 2: CẤU HÌNH AUDIO")

        audio_filter = ""
        ffmpeg_inputs = []

        # --- TRƯỜNG HỢP 1: CÓ NHẠC NỀN (Mix + Ducking) ---
        if has_music:
            duck = req.ducking_ratio or DEFAULT_DUCKING_RATIO
            atk = req.attack_time or DEFAULT_ATTACK_TIME
            rel = req.release_time or DEFAULT_RELEASE_TIME

            print(f"   🎚️  Mode: MIXING (Voice + Music)")
            print(f"   📉 Ducking Ratio: {duck} | Attack: {atk}ms | Release: {rel}ms")

            # Input: 0=Video, 1=Music, 2=Voice
            ffmpeg_inputs = ["-i", vid, "-i", inst, "-i", voice]

            audio_filter = (
                f"[2:a]volume={v_vol},lowshelf=g=5:f=100:w=0.5[voice];"  # Xử lý Voice (Input 2)
                f"[voice]asplit[v_trig][v_mix];"
                f"[1:a]volume={m_vol}[bg];"                              # Xử lý Music (Input 1)
                f"[bg][v_trig]sidechaincompress=threshold=0.1:ratio={duck}:attack={atk}:release={rel}[bg_duck];"
                f"[bg_duck][v_mix]amix=inputs=2:duration=longest[a_out]"
            )

        # --- TRƯỜNG HỢP 2: KHÔNG NHẠC (Chỉ Voice) ---
        else:
            print(f"   🎚️  Mode: VOICE ONLY (Bỏ qua nhạc nền)")

            # Input: 0=Video, 1=Voice (Bỏ qua file nhạc)
            ffmpeg_inputs = ["-i", vid, "-i", voice]

            # Chỉ xử lý Voice và gán thẳng ra [a_out]
            # Lưu ý: Voice lúc này là Input số 1 (vì không có nhạc ở giữa)
            audio_filter = (
                f"[1:a]volume={v_vol},lowshelf=g=5:f=100:w=0.5[a_out]"
            )

        # =========================================================================
        # BƯỚC 3: CẤU HÌNH VIDEO (XÓA LOGO + CHÈN CHỮ)
        # =========================================================================
        print("\n🔹 BƯỚC 3: CẤU HÌNH VIDEO FILTER")

        final_video_filter = ""
        video_map = "0:v"
        video_codec = "copy"

        if req.remove_logo:
            print("   🛡️  [MODE] Xóa Logo & Chèn Branding Text đang BẬT")

            x = req.logo_x if req.logo_x is not None else 20
            y = req.logo_y if req.logo_y is not None else 30
            w = req.logo_w if req.logo_w is not None else 250
            h = req.logo_h if req.logo_h is not None else 40
            brand_txt = req.branding_text if req.branding_text else "NQT REVIEW"

            # Font Logic
            font_cmd_part = "font='Arial'"
            font_files = [f for f in os.listdir(video_dir) if f.lower().endswith(('.ttf', '.otf'))]
            if font_files:
                raw_path = Path(video_dir) / font_files[0]
                posix_path = raw_path.as_posix().replace(":", "\\:")
                font_cmd_part = f"fontfile='{posix_path}'"

            # Filter
            delogo_cmd = f"[0:v]delogo=x={x}:y={y}:w={w}:h={h}[v_delogo];"
            drawtext_cmd = (
                f"[v_delogo]drawtext={font_cmd_part}:text='{brand_txt}':"
                f"fontcolor=white:fontsize=24:box=1:boxcolor=black@0.6:boxborderw=5:"
                f"x={x}+(({w}-text_w)/2):y={y}+(({h}-text_h)/2)[v_branded];"
            )

            final_video_filter = delogo_cmd + drawtext_cmd
            video_map = "[v_branded]"
            video_codec = "libx264"
        else:
            print("   ⏩ [SKIP] Không xóa logo -> Giữ nguyên Video Stream gốc.")

        # =========================================================================
        # BƯỚC 4: TỔNG HỢP LỆNH FFMPEG
        # =========================================================================
        print("\n🔹 BƯỚC 4: BUILD LỆNH FFMPEG")

        full_complex_filter = (final_video_filter + audio_filter) if final_video_filter else audio_filter

        # Xây dựng lệnh cơ bản
        cmd = ["ffmpeg", "-y"]

        # Thêm các Input (Động)
        cmd.extend(ffmpeg_inputs)

        # Thêm Filter và Map
        cmd.extend([
            "-filter_complex", full_complex_filter,
            "-map", video_map,
            "-map", "[a_out]",
            "-c:v", video_codec,
            "-c:a", "aac",
            "-b:a", "192k",
        ])

        if video_codec == "libx264":
            cmd.extend(["-preset", "medium", "-crf", "23"])

        cmd.append(out)

        # =========================================================================
        # BƯỚC 5: THỰC THI
        # =========================================================================
        print("\n🔹 BƯỚC 5: RUNNING...")
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE)

        elapsed = time.time() - start_time
        print("\n" + "="*70)
        print(f"   ⏱️  Tổng thời gian xử lý: {elapsed:.2f}s")
        Logger.success(f"XỬ LÝ THÀNH CÔNG!", elapsed)
        print(f"   👉 KẾT QUẢ: {out}")
        print("="*70 + "\n")

        return {"status": "success", "output_file": out}

    except subprocess.CalledProcessError as e:
        err_msg = e.stderr.decode('utf-8', errors='ignore') if e.stderr else str(e)
        print("\n❌ [FFMPEG ERROR DETAILS]")
        print("-" * 50)
        print("\n".join(err_msg.splitlines()[-20:]))
        print("-" * 50)
        Logger.error("Quá trình Mix thất bại!")
        raise HTTPException(500, f"FFmpeg Error: {err_msg}")

    except Exception as e:
        Logger.error("Lỗi hệ thống", e)
        traceback.print_exc()
        raise HTTPException(500, str(e))

if __name__ == "__main__":
    # 🔥 Bước 0: Tự động tắt process cũ chiếm port 8008
    free_port_windows(PORT)

    print(f"🚀 STARTING SERVER ON PORT {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)