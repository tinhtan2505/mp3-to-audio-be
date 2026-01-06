import os
import time
import uuid
import asyncio
import subprocess
import shutil
from datetime import datetime

# Thư viện AI & Audio
import whisper
from whisper.utils import get_writer
import edge_tts
import pysrt
import librosa
import soundfile as sf
import numpy as np

# Thư viện Web Server
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# --- KHỞI TẠO APP & LOAD MODEL ---
app = FastAPI()

print("\n" + "="*50)
print("🚀 [INIT] ĐANG KHỞI ĐỘNG SERVER TẠI PORT 8008...")
print("⏳ [INIT] ĐANG LOAD MODEL WHISPER (MEDIUM)... Vui lòng chờ!")
# Load model 1 lần duy nhất khi khởi động server
start_load = time.time()
model = whisper.load_model("medium")
print(f"✅ [INIT] LOAD MODEL WHISPER THÀNH CÔNG ({time.time() - start_load:.2f}s)")
print("="*50 + "\n")

# --- CẤU HÌNH ---
SAMPLE_RATE = 24000
VOICE_FEMALE = "vi-VN-HoaiMyNeural"
VOICE_MALE = "vi-VN-NamMinhNeural"

# Cấu hình Mix Video
MUSIC_VOLUME = 1.0
VOICE_VOLUME = 1.8
DUCKING_RATIO = 5
ATTACK_TIME = 50
RELEASE_TIME = 300

# --- DTO (DATA TRANSFER OBJECTS) ---
class WhisperRequest(BaseModel):
    input_path: str

class TtsRequest(BaseModel):
    input_srt_path: str

class MixRequest(BaseModel):
    video_input: str
    instrumental: str
    voice_dub: str

# --- CÁC HÀM HỖ TRỢ (HELPER FUNCTIONS) ---
def get_timestamp_str():
    """Tạo chuỗi thời gian thực: YYYYMMDD_HHMMSS"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")

async def generate_tts(text, voice, output_file):
    """Sinh file âm thanh từ Edge-TTS"""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_file)

def process_audio_segment(file_path, target_duration_sec):
    """Xử lý âm thanh: Load -> Time Stretch (nếu cần)"""
    try:
        y, sr = librosa.load(file_path, sr=SAMPLE_RATE)
        current_duration = len(y) / sr

        if current_duration > target_duration_sec:
            rate = current_duration / target_duration_sec
            rate = min(rate, 1.5) # Max speed 1.5x
            # print(f"   ⚠️ [TimeStretch] Audio dài hơn sub ({current_duration:.2f}s > {target_duration_sec:.2f}s). Tua nhanh x{rate:.2f}")
            y = librosa.effects.time_stretch(y, rate=rate)
        return y
    except Exception as e:
        print(f"⚠️ Lỗi xử lý segment audio: {e}")
        return np.zeros(int(target_duration_sec * SAMPLE_RATE))

# ==========================================
# API 1: WHISPER (AUDIO -> SRT)
# ==========================================
@app.post("/api/v1/dubbing/whisper")
def api_whisper(req: WhisperRequest):
    print("\n" + "="*60)
    print("📢 [BƯỚC 1 - WHISPER] BẮT ĐẦU DỊCH AUDIO SANG SRT")

    try:
        input_path = os.path.abspath(req.input_path)
        print(f"📂 File đầu vào: {input_path}")

        if not os.path.exists(input_path):
            print("❌ LỖI: File không tồn tại!")
            raise HTTPException(status_code=400, detail=f"File không tồn tại: {input_path}")

        # Xử lý tên file
        timestamp = get_timestamp_str()
        output_dir = os.path.dirname(input_path)
        filename_no_ext = os.path.splitext(os.path.basename(input_path))[0]
        prefix_name = filename_no_ext.split('_')[0]
        output_name = f"{prefix_name}_cn_{timestamp}"

        # Transcribe
        print(f"⏳ Đang chạy mô hình Whisper (Medium)... (Có thể mất vài phút)")
        start_time = time.time()
        result = model.transcribe(input_path, language="zh", fp16=False)
        process_time = time.time() - start_time
        print(f"✅ Whisper hoàn tất trong {process_time:.2f} giây.")

        # Xuất SRT
        print(f"💾 Đang lưu file SRT...")
        writer = get_writer("srt", output_dir)
        writer(result, output_name)

        full_output_path = os.path.join(output_dir, output_name + ".srt")
        print(f"🎉 [KẾT QUẢ BƯỚC 1] File SRT đã được lưu tại:")
        print(f"👉 {full_output_path}")
        print("="*60 + "\n")

        return {
            "status": "success",
            "message": "Tạo SRT thành công",
            "output_file": full_output_path
        }
    except Exception as e:
        print(f"❌ LỖI WHISPER: {str(e)}")
        print("="*60 + "\n")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# API 2: TTS (SRT -> DUBBED WAV)
# ==========================================
@app.post("/api/v1/dubbing/tts-gen")
async def api_tts_gen(req: TtsRequest):
    print("\n" + "="*60)
    print("📢 [BƯỚC 2 - TTS] BẮT ĐẦU TẠO GIỌNG ĐỌC TỪ SRT")

    try:
        input_srt = os.path.abspath(req.input_srt_path)
        print(f"📂 File SRT đầu vào: {input_srt}")

        if not os.path.exists(input_srt):
            print("❌ LỖI: File SRT không tồn tại!")
            raise HTTPException(status_code=400, detail=f"File SRT không tồn tại")

        # Xử lý tên file Output
        timestamp = get_timestamp_str()
        output_dir = os.path.dirname(input_srt)
        filename_no_ext = os.path.splitext(os.path.basename(input_srt))[0]
        prefix_name = filename_no_ext.split('_')[0]
        output_wav_name = f"{prefix_name}_audio_vi_{timestamp}.wav"
        output_wav_path = os.path.join(output_dir, output_wav_name)

        print("📖 Đang đọc nội dung file subtitle...")
        subs = pysrt.open(input_srt)
        if not subs:
            print("❌ LỖI: File SRT rỗng!")
            raise HTTPException(status_code=400, detail="File SRT rỗng")

        # Chuẩn bị mảng âm thanh tổng
        print("🧮 Đang tính toán độ dài Audio tổng...")
        total_seconds = (subs[-1].end.ordinal / 1000) + 5
        total_samples = int(total_seconds * SAMPLE_RATE)
        final_audio = np.zeros(total_samples, dtype=np.float32)

        print(f"🎙️ Bắt đầu lồng tiếng {len(subs)} câu thoại...")
        req_id = str(uuid.uuid4())[:8]
        count_ok = 0

        for i, sub in enumerate(subs):
            text = sub.text.strip()
            if not text: continue

            start_ms = sub.start.ordinal
            duration_sec = (sub.end.ordinal - sub.start.ordinal) / 1000.0

            # Logic chọn giọng & Clean text
            voice = VOICE_FEMALE
            text_upper = text.upper()
            if any(t in text_upper for t in ["[NAM]", "[M]", "[NAM_CHINH]"]):
                voice = VOICE_MALE
                for t in ["[NAM]", "[M]", "[NAM_CHINH]"]: text = text.replace(t, "")
            elif any(t in text_upper for t in ["[NU]", "[F]"]):
                for t in ["[NU]", "[F]"]: text = text.replace(t, "")

            if "]" in text and text.startswith("["): text = text.split("]", 1)[-1].strip()
            text = text.strip()
            if not text: continue

            # print(f"   🔹 Line {i+1}: {text[:30]}...")

            # Sinh Audio & Ghép
            temp_file = f"temp_{req_id}_{i}.mp3"
            try:
                await generate_tts(text, voice, temp_file)
                audio_segment = process_audio_segment(temp_file, duration_sec)

                start_sample = int((start_ms / 1000.0) * SAMPLE_RATE)
                end_sample = start_sample + len(audio_segment)

                if end_sample > len(final_audio): # Mở rộng mảng nếu tràn
                    padding = np.zeros(end_sample - len(final_audio))
                    final_audio = np.concatenate((final_audio, padding))

                # Overlay
                final_audio[start_sample:start_sample+len(audio_segment)] += audio_segment
                count_ok += 1
            except Exception as e:
                print(f"❌ Lỗi xử lý line {i}: {e}")
            finally:
                if os.path.exists(temp_file): os.remove(temp_file)

        print(f"✅ Đã xử lý xong {count_ok}/{len(subs)} câu.")
        print(f"💾 Đang xuất file WAV chất lượng cao...")
        sf.write(output_wav_path, final_audio, SAMPLE_RATE)

        print(f"🎉 [KẾT QUẢ BƯỚC 2] File Audio đã được lưu tại:")
        print(f"👉 {output_wav_path}")
        print("="*60 + "\n")

        return {
            "status": "success",
            "message": "Tạo audio thành công",
            "output_file": output_wav_path
        }
    except Exception as e:
        print(f"❌ LỖI TTS: {str(e)}")
        print("="*60 + "\n")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# API 3: MIX VIDEO (FFMPEG)
# ==========================================
@app.post("/api/v1/dubbing/mix-video")
def api_mix_video(req: MixRequest):
    print("\n" + "="*60)
    print("📢 [BƯỚC 3 - MIX] BẮT ĐẦU HÒA ÂM VÀ XUẤT VIDEO")

    try:
        video_input = os.path.abspath(req.video_input)
        instrumental = os.path.abspath(req.instrumental)
        voice_dub = os.path.abspath(req.voice_dub)

        print(f"📂 Các file đầu vào:")
        print(f"   🎥 Video Gốc : {video_input}")
        print(f"   🎵 Nhạc Nền  : {instrumental}")
        print(f"   🗣️ Giọng Đọc : {voice_dub}")

        if not all(os.path.exists(f) for f in [video_input, instrumental, voice_dub]):
            print("❌ LỖI: Một trong các file đầu vào không tồn tại!")
            raise HTTPException(status_code=400, detail="Thiếu file đầu vào")

        # Output Name
        timestamp = get_timestamp_str()
        output_dir = os.path.dirname(video_input)
        filename_no_ext = os.path.splitext(os.path.basename(video_input))[0]
        prefix_name = filename_no_ext.split('_')[0]
        output_name = f"{prefix_name}_video_vi_{timestamp}.mp4"
        output_full_path = os.path.join(output_dir, output_name)

        print(f"⚙️ Cấu hình FFmpeg Sidechain Compression:")
        print(f"   - Voice Volume : {VOICE_VOLUME}")
        print(f"   - Music Volume : {MUSIC_VOLUME}")
        print(f"   - Ducking Ratio: {DUCKING_RATIO}")

        # FFmpeg Command
        filter_complex = (
            f"[2:a]volume={VOICE_VOLUME},lowshelf=g=5:f=100:w=0.5[voice_proc];"
            f"[voice_proc]asplit[voice_trigger][voice_mix];"
            f"[1:a]volume={MUSIC_VOLUME}[bg_ready];"
            f"[bg_ready][voice_trigger]sidechaincompress="
            f"threshold=0.1:ratio={DUCKING_RATIO}:attack={ATTACK_TIME}:release={RELEASE_TIME}"
            f"[bg_ducked];"
            f"[bg_ducked][voice_mix]amix=inputs=2:duration=longest[audio_out]"
        )

        command = [
            "ffmpeg", "-y",
            "-i", video_input,
            "-i", instrumental,
            "-i", voice_dub,
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[audio_out]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            output_full_path
        ]

        print("🎬 Đang chạy FFmpeg... Vui lòng không tắt cửa sổ!")
        subprocess.run(command, check=True)
        print(f"✅ FFmpeg xử lý thành công.")

        print(f"🎉 [KẾT QUẢ BƯỚC 3] Video hoàn chỉnh đã được lưu tại:")
        print(f"👉 {output_full_path}")
        print("="*60 + "\n")

        return {
            "status": "success",
            "message": "Hòa âm video thành công",
            "output_file": output_full_path
        }
    except subprocess.CalledProcessError as e:
        print(f"❌ LỖI FFMPEG: {str(e)}")
        print("="*60 + "\n")
        raise HTTPException(status_code=500, detail="Lỗi FFmpeg")
    except Exception as e:
        print(f"❌ LỖI MIX: {str(e)}")
        print("="*60 + "\n")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # In ra dòng này để người dùng biết server đã sẵn sàng
    print("🚀SERVER ĐANG SẴN SÀNG TẠI PORT 8008...")
    uvicorn.run(app, host="0.0.0.0", port=8008)