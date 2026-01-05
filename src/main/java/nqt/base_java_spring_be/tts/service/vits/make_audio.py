import os
import edge_tts
import pysrt
import librosa
import soundfile as sf
import numpy as np
import uuid
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# --- CẤU HÌNH ---
SAMPLE_RATE = 24000
VOICE_FEMALE = "vi-VN-HoaiMyNeural"
VOICE_MALE = "vi-VN-NamMinhNeural"

app = FastAPI()

class TtsRequest(BaseModel):
    input_srt_path: str

async def generate_tts(text, voice, output_file):
    """Sinh file âm thanh từ Edge-TTS"""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_file)

def process_audio_segment(file_path, target_duration_sec):
    """Xử lý âm thanh: Load -> Co dãn thời gian bằng Librosa"""
    try:
        # Load audio (Librosa load trả về (data, sample_rate))
        y, sr = librosa.load(file_path, sr=SAMPLE_RATE)
        current_duration = len(y) / sr

        if current_duration > target_duration_sec:
            rate = current_duration / target_duration_sec
            rate = min(rate, 1.5) # Giới hạn max speed 1.5x
            y = librosa.effects.time_stretch(y, rate=rate)

        return y
    except Exception as e:
        print(f"Lỗi xử lý audio segment: {e}")
        return np.zeros(int(target_duration_sec * SAMPLE_RATE))

@app.post("/api/v1/tts-gen")
async def api_tts_gen(req: TtsRequest):
    input_srt = req.input_srt_path
    print(f"\n[PORT 8002] Nhận request SRT: {input_srt}")

    if not os.path.exists(input_srt):
        raise HTTPException(status_code=400, detail=f"File SRT không tồn tại: {input_srt}")

    # --- XỬ LÝ TÊN FILE OUTPUT ---
    # Input: D:\Dubbing\pmh_vi.srt
    # Mong muốn: D:\Dubbing\pmh_audio_vi.wav

    output_dir = os.path.dirname(input_srt)
    filename_w_ext = os.path.basename(input_srt)          # pmh_vi.srt
    filename_no_ext = os.path.splitext(filename_w_ext)[0] # pmh_vi

    # Logic cắt tên: lấy phần đầu trước dấu gạch dưới (pmh)
    prefix_name = filename_no_ext.split('_')[0]           # pmh

    # Tạo tên file output
    output_wav_name = f"{prefix_name}_audio_vi.wav"       # pmh_audio_vi.wav
    output_wav_path = os.path.join(output_dir, output_wav_name)

    try:
        print("📖 Đang đọc file subtitle...")
        subs = pysrt.open(input_srt)
        if not subs:
            raise HTTPException(status_code=400, detail="File SRT rỗng")

        # Tính tổng độ dài (thêm 5s padding cuối)
        total_seconds = (subs[-1].end.ordinal / 1000) + 5
        total_samples = int(total_seconds * SAMPLE_RATE)
        final_audio = np.zeros(total_samples, dtype=np.float32)

        print(f"🎙️ Bắt đầu lồng tiếng {len(subs)} câu thoại...")
        req_id = str(uuid.uuid4())[:8] # ID định danh cho request này để tránh trùng file tạm

        for i, sub in enumerate(subs):
            text = sub.text.strip()
            start_ms = sub.start.ordinal
            end_ms = sub.end.ordinal
            duration_sec = (end_ms - start_ms) / 1000.0

            # 1. Logic chọn giọng
            voice = VOICE_FEMALE
            text_upper = text.upper()

            # Xử lý các tag [NAM], [NU]...
            if any(tag in text_upper for tag in ["[NAM]", "[M]", "[NAM_CHINH]"]):
                voice = VOICE_MALE
                for tag in ["[NAM]", "[M]", "[NAM_CHINH]"]:
                    text = text.replace(tag, "")
            elif any(tag in text_upper for tag in ["[NU]", "[F]"]):
                for tag in ["[NU]", "[F]"]:
                    text = text.replace(tag, "")

            # Xử lý tag tên nhân vật [Name]:
            if "]" in text and text.startswith("["):
                text = text.split("]", 1)[-1].strip()

            text = text.strip()
            if not text: continue

            # 2. Sinh file tạm & Xử lý
            temp_file = f"temp_{req_id}_{i}.mp3"
            try:
                await generate_tts(text, voice, temp_file)

                audio_segment = process_audio_segment(temp_file, duration_sec)

                # 3. Ghép vào mảng tổng
                start_sample = int((start_ms / 1000.0) * SAMPLE_RATE)
                end_sample = start_sample + len(audio_segment)

                # Nới rộng mảng nếu cần (phòng trường hợp sub cuối bị tràn)
                if end_sample > len(final_audio):
                    padding = np.zeros(end_sample - len(final_audio))
                    final_audio = np.concatenate((final_audio, padding))

                # Cộng dồn âm thanh (Overlay)
                current_slice = final_audio[start_sample:end_sample]
                if len(current_slice) < len(audio_segment):
                    audio_segment = audio_segment[:len(current_slice)]

                final_audio[start_sample:start_sample+len(audio_segment)] += audio_segment

            except Exception as e:
                print(f"❌ Lỗi line {i+1}: {e}")
            finally:
                if os.path.exists(temp_file):
                    os.remove(temp_file)

        # Xuất file WAV
        print("💾 Đang xuất file WAV...")
        sf.write(output_wav_path, final_audio, SAMPLE_RATE)
        print(f"🎉 XONG! File: {output_wav_path}")

        return {
            "status": "success",
            "message": "Tạo audio thành công",
            "input_file": input_srt,
            "output_file": output_wav_path
        }

    except Exception as e:
        print(f"LỖI SERVER: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    print("TTS Server đang chạy tại http://localhost:8002")
    uvicorn.run(app, host="0.0.0.0", port=8002)