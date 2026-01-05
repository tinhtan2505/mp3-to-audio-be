import asyncio
import edge_tts
import pysrt
import librosa
import soundfile as sf
import numpy as np
import os

# --- CẤU HÌNH ---
INPUT_SRT = "output_vi.srt"       # File sub tiếng Việt
OUTPUT_AUDIO = "final_dub.wav"    # File kết quả
SAMPLE_RATE = 24000               # Tần số lấy mẫu chuẩn của Edge-TTS

# Giọng đọc
VOICE_FEMALE = "vi-VN-HoaiMyNeural"
VOICE_MALE = "vi-VN-NamMinhNeural"

async def generate_tts(text, voice, output_file):
    """Sinh file âm thanh từ Edge-TTS"""
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_file)

def process_audio_segment(file_path, target_duration_sec):
    """Xử lý âm thanh: Load -> Co dãn thời gian (nếu cần)"""
    # Load audio bằng librosa
    y, sr = librosa.load(file_path, sr=SAMPLE_RATE)

    current_duration = len(y) / sr

    if current_duration > target_duration_sec:
        # Nếu audio dài hơn phim -> Tăng tốc
        rate = current_duration / target_duration_sec
        # Giới hạn tốc độ tối đa 1.5x để nghe cho rõ
        rate = min(rate, 1.5)

        # Dùng thuật toán time_stretch xịn của librosa (giữ nguyên cao độ)
        y = librosa.effects.time_stretch(y, rate=rate)
        print(f" -> Tua nhanh x{rate:.2f}")

    return y

async def main():
    if not os.path.exists(INPUT_SRT):
        print(f"❌ Không tìm thấy file {INPUT_SRT}")
        return

    print("📖 Đang đọc file subtitle...")
    subs = pysrt.open(INPUT_SRT)

    # Tính tổng độ dài phim (đổi sang số mẫu - samples)
    # Thêm 5 giây dư ở cuối cho an toàn
    total_seconds = (subs[-1].end.ordinal / 1000) + 5
    total_samples = int(total_seconds * SAMPLE_RATE)

    # Tạo một mảng chứa âm thanh rỗng (im lặng)
    final_audio = np.zeros(total_samples, dtype=np.float32)

    print(f"🎙️ Bắt đầu lồng tiếng {len(subs)} câu thoại (Dùng Librosa Engine)...")

    for i, sub in enumerate(subs):
        text = sub.text.strip()
        start_ms = sub.start.ordinal
        end_ms = sub.end.ordinal
        duration_sec = (end_ms - start_ms) / 1000.0

        # 1. Chọn giọng
        voice = VOICE_FEMALE
        if "[NAM]" in text.upper() or "[M]" in text.upper() or "[NAM_CHINH]" in text.upper():
            voice = VOICE_MALE
            text = text.replace("[NAM]", "").replace("[M]", "").replace("[NAM_CHINH]", "").strip()
        elif "[NU]" in text.upper() or "[F]" in text.upper():
            text = text.replace("[NU]", "").replace("[F]", "").strip()

        # Làm sạch các tag SPEAKER nếu còn sót
        if "]" in text and text.startswith("["):
            text = text.split("]", 1)[-1].strip()

        if not text: continue

        # 2. Sinh file tạm
        temp_file = f"temp_{i}.mp3"
        try:
            await generate_tts(text, voice, temp_file)

            # 3. Xử lý audio (Load + Stretch)
            audio_segment = process_audio_segment(temp_file, duration_sec)

            # 4. Ghép vào mảng tổng
            start_sample = int((start_ms / 1000.0) * SAMPLE_RATE)
            end_sample = start_sample + len(audio_segment)

            # Đảm bảo không ghi tràn mảng
            if end_sample > len(final_audio):
                # Nới rộng mảng nếu cần
                padding = np.zeros(end_sample - len(final_audio))
                final_audio = np.concatenate((final_audio, padding))

            # Cộng dồn âm thanh (Overlay)
            final_audio[start_sample:end_sample] += audio_segment

            # print(f"✅ Line {i+1} OK")

        except Exception as e:
            print(f"❌ Lỗi line {i+1}: {e}")

        # Dọn dẹp file tạm
        if os.path.exists(temp_file):
            try: os.remove(temp_file)
            except: pass

    # Xuất file
    print("💾 Đang xuất file WAV chất lượng cao...")
    sf.write(OUTPUT_AUDIO, final_audio, SAMPLE_RATE)
    print(f"🎉 XONG! File lồng tiếng đã lưu tại: {OUTPUT_AUDIO}")

if __name__ == "__main__":
    asyncio.run(main())