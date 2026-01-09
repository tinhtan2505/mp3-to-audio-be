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

def process_audio_segment(file_path, target_duration_sec):
    try:
        y, sr = librosa.load(file_path, sr=SAMPLE_RATE)
        current_len = len(y) / sr
        if current_len > target_duration_sec:
            rate = min(current_len / target_duration_sec, 1.5)
            y = librosa.effects.time_stretch(y, rate=rate)
        return y
    except:
        return np.zeros(int(target_duration_sec * SAMPLE_RATE))

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

        total_len = (subs[-1].end.ordinal/1000) + 5
        final_audio = np.zeros(int(total_len * SAMPLE_RATE), dtype=np.float32)
        count = 0

        print(f"   🎙️  TTS {len(subs)} câu...")
        for i, sub in enumerate(subs):
            txt = sub.text.strip().upper()
            if not txt: continue

            voice = VOICE_MALE if ("[NAM" in txt or "[M]" in txt) else VOICE_FEMALE
            clean_txt = re.sub(r"^\[.*?\]", "", sub.text).strip()
            if not clean_txt: continue

            tmp = f"t_{uuid.uuid4().hex}.mp3"
            try:
                await generate_tts(clean_txt, voice, tmp)
                dur = (sub.end.ordinal - sub.start.ordinal)/1000
                seg = process_audio_segment(tmp, dur)

                start = int((sub.start.ordinal/1000)*SAMPLE_RATE)
                end = start + len(seg)
                if end > len(final_audio):
                    final_audio = np.concatenate((final_audio, np.zeros(end-len(final_audio))))
                final_audio[start:end] += seg
                count += 1
            finally:
                if os.path.exists(tmp): os.remove(tmp)

        out_name = f"{path.replace('.srt', '')}_audio_vi_{get_timestamp_str()}.wav"
        sf.write(out_name, final_audio, SAMPLE_RATE)
        Logger.success(f"TTS Done: {out_name}")
        return {"status": "success", "output_file": out_name}
    except Exception as e:
        Logger.error("TTS Error", e)
        raise HTTPException(500, str(e))

@app.post("/api/v1/dubbing/mix-video")
def api_mix(req: MixRequest):
    try:
        vid, inst, voice = req.video_input, req.instrumental, req.voice_dub
        out = os.path.join(os.path.dirname(vid), f"out_vi_{get_timestamp_str()}.mp4")

        v_vol = req.voice_volume or DEFAULT_VOICE_VOLUME
        m_vol = req.music_volume or DEFAULT_MUSIC_VOLUME
        duck = req.ducking_ratio or DEFAULT_DUCKING_RATIO
        atk = req.attack_time or DEFAULT_ATTACK_TIME
        rel = req.release_time or DEFAULT_RELEASE_TIME

        filter = (f"[2:a]volume={v_vol},lowshelf=g=5:f=100:w=0.5[voice];"
                  f"[voice]asplit[v_trig][v_mix];"
                  f"[1:a]volume={m_vol}[bg];"
                  f"[bg][v_trig]sidechaincompress=threshold=0.1:ratio={duck}:attack={atk}:release={rel}[bg_duck];"
                  f"[bg_duck][v_mix]amix=inputs=2:duration=longest[a_out]")

        cmd = ["ffmpeg", "-y", "-i", vid, "-i", inst, "-i", voice,
               "-filter_complex", filter, "-map", "0:v", "-map", "[a_out]",
               "-c:v", "copy", "-c:a", "aac", out]

        print("   🎬 FFmpeg Processing...")
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE)
        Logger.success(f"Mix Done: {out}")
        return {"status": "success", "output_file": out}
    except Exception as e:
        Logger.error("Mix Error", e)
        raise HTTPException(500, str(e))

if __name__ == "__main__":
    # 🔥 Bước 0: Tự động tắt process cũ chiếm port 8008
    free_port_windows(PORT)

    print(f"🚀 STARTING SERVER ON PORT {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)