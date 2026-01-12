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
import edge_tts
from pathlib import Path

# 🔥 FIX LỖI IMPORT CŨ: Dùng alias để tránh xung đột
from transformers import pipeline as hf_pipeline

# --- PYANNOTE SETUP ---
import huggingface_hub
from pyannote.audio import Pipeline
from pyannote.core import Segment

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
HF_TOKEN = ""
PORT = 8008  # 🔥 Cổng cố định

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
    "pyannote": None,
    "gender": None,
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
    Logger.section("BƯỚC 2: LOAD AI MODELS")
    device = torch.device(AI_MODELS["device"])
    device_id = 0 if AI_MODELS["device"] == "cuda" else -1

    # 1. Whisper
    print("\n⏳ [1/3] Loading Whisper...")
    start = time.time()
    try:
        AI_MODELS["whisper"] = whisper.load_model("medium", device=AI_MODELS["device"])
        Logger.success(f"Whisper loaded on {AI_MODELS['device'].upper()}", time.time() - start)
    except Exception as e:
        Logger.error("Lỗi Whisper", e)

    # 2. Pyannote
    print("\n⏳ [2/3] Loading Pyannote...")
    start = time.time()
    try:
        if not HF_TOKEN:
            raise ValueError("Thiếu HF_TOKEN")

        huggingface_hub.login(token=HF_TOKEN)

        # 🔥 FIX: Load với use_auth_token và đảm bảo device là torch.device
        pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=HF_TOKEN
        )

        # 🔥 FIX: Force tất cả sub-models lên GPU
        pipeline.to(device)

        # 🔥 FIX: Verify device (Pyannote dùng Inference wrapper)
        print(f"   📍 Pyannote moved to: {device}")

        AI_MODELS["pyannote"] = pipeline
        Logger.success(f"Pyannote loaded on {device}", time.time() - start)

    except Exception as e:
        Logger.error("Lỗi Pyannote", e)
        Logger.warning("Hệ thống sẽ chạy không có Speaker Diarization")

    # 3. Gender Classifier
    print("\n⏳ [3/3] Loading Gender Model...")
    start = time.time()
    try:
        # 🔥 FIX: Dùng GPU nếu có, CPU nếu không
        classifier = hf_pipeline(
            "audio-classification",
            model="alefiury/wav2vec2-large-xlsr-53-gender-recognition-librispeech",
            device=device_id  # 0 = GPU, -1 = CPU
        )
        AI_MODELS["gender"] = classifier

        device_name = "GPU (CUDA)" if device_id == 0 else "CPU"
        Logger.success(f"Gender Model loaded on {device_name}", time.time() - start)

    except Exception as e:
        Logger.error("Lỗi Gender Model", e)
        Logger.warning("Hệ thống sẽ mặc định giọng Nữ cho tất cả speaker")

    # 🔥 THÊM: In thông tin cuối cùng
    print("\n" + "="*60)
    print("📊 TỔNG KẾT THIẾT BỊ:")
    print(f"   • PyTorch device: {device}")
    print(f"   • Whisper: {AI_MODELS['device'].upper()}")
    print(f"   • Pyannote: {'GPU' if device_id == 0 else 'CPU'}")
    print(f"   • Gender: {'GPU' if device_id == 0 else 'CPU'}")
    if torch.cuda.is_available():
        print(f"   • VRAM Available: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f} GB")
    print("="*60)

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
async def generate_tts(text, voice, output_file):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_file)

def get_gender_from_ai(audio_path, start, dur):
    if AI_MODELS["gender"] is None: return "NU"
    try:
        y, sr = librosa.load(audio_path, sr=16000, offset=start, duration=min(dur, 3.0))
        tmp = f"temp_{uuid.uuid4().hex}.wav"
        sf.write(tmp, y, 16000)
        res = AI_MODELS["gender"](tmp)
        if os.path.exists(tmp): os.remove(tmp)
        return "NU" if "female" in res[0]['label'].lower() else "NAM"
    except: return "NU"

def create_speaker_mapping(diarization, path):
    mapping = {}
    m, f = 0, 0
    print(f"   🔍 Phân tích giới tính {len(diarization.labels())} người...")
    for label in diarization.labels():
        seg = max(diarization.label_timeline(label), key=lambda s: s.duration)
        gender = get_gender_from_ai(path, seg.start, seg.duration)
        if gender == "NAM": m+=1; new_l = f"NAM_{m:02d}"
        else: f+=1; new_l = f"NU_{f:02d}"
        mapping[label] = new_l
        print(f"      👉 {label} -> {new_l} ({gender})")
    return mapping

def align_whisper(whisper_res, diarization, mapping):
    for seg in whisper_res["segments"]:
        t = Segment(seg["start"], seg["end"])
        spk = diarization.crop(t).argmax()
        if spk:
            l = mapping.get(spk, spk)
            seg["text"] = f"[{l}] {seg['text'].strip()}"
    return whisper_res

# --- DTO ---
class WhisperRequest(BaseModel): input_path: str
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
    if not AI_MODELS["whisper"]: raise HTTPException(500, "Whisper chưa load")
    try:
        path = os.path.abspath(req.input_path)
        if not os.path.exists(path): raise FileNotFoundError("File không tồn tại")

        print("   ⏳ [1/2] Transcribing...")
        w_res = AI_MODELS["whisper"].transcribe(path, language="zh", fp16=False)

        if AI_MODELS["pyannote"]:
            print("   ⏳ [2/2] Diarization...")
            try:
                d_res = AI_MODELS["pyannote"](path)
                mapping = create_speaker_mapping(d_res, path)
                w_res = align_whisper(w_res, d_res, mapping)
            except Exception as e:
                Logger.error("Lỗi Diarization", e)

        out_dir = os.path.dirname(path)
        base = os.path.splitext(os.path.basename(path))[0].split('_')[0]
        out_name = f"{base}_cn_{get_timestamp_str()}"

        get_writer("srt", out_dir)(w_res, out_name)
        full = os.path.join(out_dir, out_name + ".srt")
        Logger.success(f"SRT saved: {full}")
        return {"status": "success", "output_file": full}
    except Exception as e:
        Logger.error("Whisper Error", e)
        raise HTTPException(500, str(e))

@app.post("/api/v1/dubbing/tts-gen")
async def api_tts(req: TtsRequest):
    try:
        path = os.path.abspath(req.input_srt_path)
        subs = pysrt.open(path)
        if not subs: raise ValueError("SRT rỗng")

        # Tạo buffer tổng (ước lượng)
        total_seconds = (subs[-1].end.ordinal / 1000.0) + 20.0
        final_audio = np.zeros(int(total_seconds * SAMPLE_RATE), dtype=np.float32)

        last_end_sample = 0
        SAFETY_GAP = 0.1  # Giữ khoảng cách 0.1s giữa các câu để không dính chùm

        print("\n" + "="*80)
        print(f"🎙️  SMART TTS GENERATOR (GAP BORROWING MODE)")
        print(f"   • Input: {os.path.basename(path)}")
        print(f"   • Logic: Tận dụng khoảng lặng (Gap) để hạn chế tua tiếng")
        print("="*80)

        for i, sub in enumerate(subs):
            # 1. Lọc text
            txt_raw = sub.text.strip()
            clean_txt = re.sub(r"^\[.*?\]", "", txt_raw).strip()
            if not clean_txt: continue

            # 2. Xác định Voice
            is_male = "[NAM" in txt_raw.upper() or "[M]" in txt_raw.upper()
            voice = VOICE_MALE if is_male else VOICE_FEMALE
            voice_icon = "👦" if is_male else "👩"

            # 3. Tính toán thời gian (Time Calculation)
            start_seconds = sub.start.ordinal / 1000.0
            end_seconds = sub.end.ordinal / 1000.0
            slot_duration = end_seconds - start_seconds

            # --- 🔥 LOGIC XÁC ĐỊNH GIỚI HẠN (HARD LIMIT) ---
            # Giới hạn là thời điểm bắt đầu của câu TIẾP THEO (trừ đi safety gap)
            if i < len(subs) - 1:
                next_start = subs[i+1].start.ordinal / 1000.0
                hard_limit = next_start - SAFETY_GAP
                gap_info = f"Next sub at {next_start}s"
            else:
                hard_limit = end_seconds + 5.0 # Câu cuối thả ga
                gap_info = "Last sentence"

            # Đảm bảo Hard Limit không được nhỏ hơn thời gian kết thúc gốc (đề phòng sub lỗi)
            hard_limit = max(hard_limit, end_seconds)

            # Xác định điểm bắt đầu thực tế (Nếu câu trước bị trễ, câu này phải trễ theo)
            actual_start_sample = max(int(start_seconds * SAMPLE_RATE), last_end_sample)
            actual_start_seconds = actual_start_sample / SAMPLE_RATE

            # KHOẢNG TRỐNG TỐI ĐA CHO PHÉP (Available Space)
            available_space = hard_limit - actual_start_seconds

            tmp_file = f"temp_{uuid.uuid4().hex}.mp3"

            try:
                # 4. Generate TTS & Trim
                await generate_tts(clean_txt, voice, tmp_file)
                y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
                y_trimmed, _ = librosa.effects.trim(y, top_db=30)

                audio_duration = len(y_trimmed) / SAMPLE_RATE

                # 5. So sánh & Quyết định Tốc độ (Decision Logic)
                final_rate = 1.0
                action_log = ""
                status_icon = "✅"

                # Case A: Audio ngắn hơn Slot gốc -> Quá tốt
                if audio_duration <= slot_duration:
                    final_rate = 1.0
                    status_icon = "✨ PERFECT"
                    action_log = "Vừa khít slot gốc"

                # Case B: Audio dài hơn Slot gốc NHƯNG ngắn hơn Khoảng trống cho phép -> MƯỢN GAP
                elif audio_duration <= available_space:
                    final_rate = 1.0
                    status_icon = "👌 BORROW"
                    borrow_time = audio_duration - slot_duration
                    action_log = f"Mượn {borrow_time:.2f}s từ khoảng lặng"

                # Case C: Audio quá dài so với cả Khoảng trống -> PHẢI TUA (SPEED UP)
                else:
                    if available_space > 0.1: # Nếu còn chút không gian nào đó
                        calc_rate = audio_duration / available_space
                        # Chỉ cho phép tua tối đa 1.35x để đỡ méo tiếng
                        if calc_rate <= 1.35:
                            final_rate = calc_rate
                            status_icon = "⚡ SPEED"
                            action_log = f"Tua nhanh {final_rate:.2f}x để vừa Gap"
                        else:
                            final_rate = 1.35
                            status_icon = "🐢 LAG"
                            over_time = (audio_duration / 1.35) - available_space
                            action_log = f"Max tua 1.35x (Vẫn trễ {over_time:.2f}s)"
                    else:
                        # Trường hợp hiếm: Không còn chỗ trống (câu trước lấn hết)
                        final_rate = 1.4
                        status_icon = "❌ CRIT"
                        action_log = "Không còn Gap, ép tua 1.4x"

                # 6. Xử lý Audio
                if final_rate > 1.01:
                    y_final = librosa.effects.time_stretch(y_trimmed, rate=final_rate)
                else:
                    y_final = y_trimmed

                # 7. Ghép vào Timeline
                current_len = len(y_final)
                end_sample = actual_start_sample + current_len

                if end_sample > len(final_audio):
                    padding = np.zeros(end_sample - len(final_audio) + SAMPLE_RATE, dtype=np.float32)
                    final_audio = np.concatenate((final_audio, padding))

                final_audio[actual_start_sample:end_sample] += y_final
                last_end_sample = end_sample

                # 8. PRINT LOG CHI TIẾT
                display_text = (clean_txt[:50] + "...") if len(clean_txt) > 50 else clean_txt

                print(f"   [{i+1:03d}] {voice_icon} [{sub.start} -> {sub.end}]")
                print(f"         📝 Text : {display_text}")
                print(f"         ⏱️  Time : Slot Gốc: {slot_duration:.2f}s | Audio Gốc: {audio_duration:.2f}s | Gap Dư: {available_space:.2f}s")
                print(f"         ⚙️  Xử lý: {status_icon} Rate {final_rate:.2f}x | {action_log}")
                print("   " + "-"*60)

            except Exception as e:
                print(f"❌ [LỖI CÂU {i+1}] {e}")
            finally:
                if os.path.exists(tmp_file): os.remove(tmp_file)

        # Cắt file đúng độ dài
        final_valid_len = max(last_end_sample, int(subs[-1].end.ordinal/1000 * SAMPLE_RATE))
        final_audio = final_audio[:final_valid_len + int(0.5*SAMPLE_RATE)] # Thêm 0.5s đuôi

        out_name = f"{path.replace('.srt', '')}_audio_{get_timestamp_str()}.wav"
        sf.write(out_name, final_audio, SAMPLE_RATE)

        print("="*80)
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