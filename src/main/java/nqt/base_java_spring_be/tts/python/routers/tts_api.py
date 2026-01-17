import os
import time
import uuid
import re
import librosa
import soundfile as sf
import numpy as np
import pysrt
from fastapi import APIRouter, HTTPException
from schemas import TtsRequest
from config import SAMPLE_RATE, VOICE_MALE, VOICE_FEMALE
from utils import Logger, generate_tts, get_timestamp_str

router = APIRouter()

# --- 5.4. API TTS (TẠO GIỌNG ĐỌC THÔNG MINH) ---
@router.post("/api/v1/dubbing/tts-gen")
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