import os
import time
import uuid
import subprocess
from datetime import datetime

import whisper
from whisper.utils import get_writer
import edge_tts
import pysrt
import librosa
import soundfile as sf
import numpy as np

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import torch
import sys
import warnings
import torchaudio
import re

if not hasattr(torchaudio, "get_audio_backend"):
    torchaudio.get_audio_backend = lambda: None
if not hasattr(torchaudio, "set_audio_backend"):
    torchaudio.set_audio_backend = lambda x: None

try:
    import speechbrain.inference
    sys.modules["speechbrain.pretrained"] = speechbrain.inference
except ImportError:
    pass

warnings.filterwarnings("ignore", category=UserWarning, module="pyannote")
warnings.filterwarnings("ignore", category=UserWarning, module="speechbrain")

from pyannote.audio import Pipeline
import huggingface_hub
from pyannote.core import Segment
from transformers import pipeline

# --- CẤU HÌNH PYANNOTE ---
# ⚠️ QUAN TRỌNG: Thay thế bằng Token Hugging Face thực của bạn
HF_TOKEN = ""

def init_pyannote_engine():
    """
    Hàm khởi tạo và kiểm tra kết nối tới Model Diarization.
    (Đã sửa lỗi xác thực Token)
    """
    print("\n" + "="*50)
    print("🕵️ [INIT] ĐANG KẾT NỐI PYANNOTE AUDIO (DIARIZATION)...")

    start_load = time.time()

    # 1. Kiểm tra thiết bị (GPU/CPU)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"⚙️ [PYANNOTE] Thiết bị sử dụng: {str(device).upper()}")

    if device.type == 'cpu':
        print("⚠️ CẢNH BÁO: Chạy Pyannote trên CPU sẽ rất chậm (khoảng 10x so với GPU)!")

    try:
        # 2. Đăng nhập Hugging Face (Cách an toàn nhất)
        print("🔑 Đang xác thực với Hugging Face...")
        huggingface_hub.login(token=HF_TOKEN)

        # 3. Load Pipeline
        # Lưu ý: Không truyền tham số use_auth_token hay token vào đây nữa
        pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")

        if pipeline is None:
            raise ValueError("Không thể tải model. Hãy kiểm tra lại mạng hoặc quyền truy cập.")

        # 4. Chuyển model sang GPU nếu có
        pipeline.to(device)

        load_time = time.time() - start_load
        print(f"✅ [INIT] LOAD PYANNOTE THÀNH CÔNG ({load_time:.2f}s)")
        print("="*50 + "\n")

        return pipeline

    except Exception as e:
        print(f"❌ [INIT] LỖI KHỞI TẠO PYANNOTE: {str(e)}")
        print("💡 GỢI Ý 1: Đảm bảo bạn đã 'Accept License' model pyannote/speaker-diarization-3.1 trên Hugging Face.")
        print("💡 GỢI Ý 2: Đảm bảo Token của bạn là loại 'Write' hoặc 'Read' còn hiệu lực.")
        print("="*50 + "\n")
        return None

# --- KHỞI TẠO APP & LOAD MODEL ---
app = FastAPI()

print("\n" + "="*50)
print("🚀 [INIT] ĐANG KHỞI ĐỘNG SERVER TẠI PORT 8008...")
print("⏳ [INIT] ĐANG LOAD MODEL WHISPER (MEDIUM)... Vui lòng chờ!")
# Load model 1 lần duy nhất khi khởi động server
start_load = time.time()
model = whisper.load_model("medium")
print(f"✅ [INIT] LOAD MODEL WHISPER THÀNH CÔNG ({time.time() - start_load:.2f}s)")
diarization_pipeline = init_pyannote_engine()

# Nếu diarization_pipeline là None (lỗi), bạn có thể quyết định dừng server hoặc chạy chế độ không phân biệt người nói.
if diarization_pipeline is None:
    print("⚠️ Server sẽ chạy mà không có tính năng phân biệt giọng nói (Diarization).")

print("⏳ [INIT] ĐANG LOAD MODEL GENDER CLASSIFICATION (Wav2Vec2)...")
# Sử dụng model chuyên dụng để phân biệt Nam/Nữ (độ chính xác >90%)
gender_classifier = pipeline("audio-classification", model="alefiury/wav2vec2-large-xlsr-53-gender-recognition-librispeech")
print("✅ [INIT] LOAD MODEL GENDER THÀNH CÔNG")
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

def align_whisper_with_diarization(whisper_result, diarization_result, speaker_map):
    """
    Hàm trộn kết quả Whisper và Pyannote, sau đó đổi tên theo Gender Map.
    """
    segments = whisper_result["segments"]

    # Duyệt qua từng câu thoại của Whisper
    for segment in segments:
        start = segment["start"]
        end = segment["end"]

        # Tạo segment pyannote
        t = Segment(start, end)

        # Cắt lấy các lượt nói trong khoảng thời gian này
        speakers_in_segment = diarization_result.crop(t)

        # Tìm speaker chiếm thời lượng lớn nhất
        dominant_speaker = None
        max_duration = 0

        for turn, _, speaker in speakers_in_segment.itertracks(yield_label=True):
            turn_start = max(start, turn.start)
            turn_end = min(end, turn.end)
            duration = turn_end - turn_start

            if duration > max_duration:
                max_duration = duration
                dominant_speaker = speaker

        # Nếu tìm thấy người nói -> Đổi tên theo Map -> Gắn vào text
        if dominant_speaker:
            # Lấy tên mới (VD: NAM_01) từ map. Nếu không có thì giữ nguyên SPEAKER_xx
            final_label = speaker_map.get(dominant_speaker, dominant_speaker)
            segment["text"] = f"[{final_label}] {segment['text'].strip()}"

    return whisper_result

# --- CÁC HÀM HỖ TRỢ AI MỚI ---

def get_gender_from_ai(audio_path, start_sec, duration_sec):
    """
    Dùng AI Model (Wav2Vec2) để phán đoán giới tính.
    Chính xác hơn nhiều so với đo Pitch (Hz).
    """
    try:
        # 1. Cắt đoạn âm thanh tạm thời để đưa vào model
        # Load 16kHz vì model Wav2Vec2 yêu cầu sample rate này
        # Chỉ lấy tối đa 3 giây để phân tích cho nhanh và tránh quá tải RAM
        y, sr = librosa.load(audio_path, sr=16000, offset=start_sec, duration=min(duration_sec, 3.0))

        # Tạo tên file tạm ngẫu nhiên để tránh xung đột
        temp_segment_path = f"temp_gender_{uuid.uuid4().hex}.wav"
        sf.write(temp_segment_path, y, 16000)

        # 2. Đưa vào Model AI dự đoán
        # Result sẽ có dạng list: [{'label': 'female', 'score': 0.99}, ...]
        result = gender_classifier(temp_segment_path)

        # 3. Xóa file tạm ngay sau khi dùng xong
        if os.path.exists(temp_segment_path):
            os.remove(temp_segment_path)

        # 4. Xử lý kết quả
        top_result = result[0]
        label = top_result['label'].lower() # 'female' hoặc 'male'

        # Debug nhẹ để xem độ tự tin của model
        # print(f"      ---> AI Score: {label} ({top_result['score']:.2f})")

        if "female" in label:
            return "NU"
        else:
            return "NAM"

    except Exception as e:
        print(f"⚠️ Lỗi AI check gender: {e}")
        # Fallback: Nếu lỗi thì mặc định là Nữ để an toàn
        return "NU"

def create_speaker_mapping(diarization_result, audio_path):
    """
    Tạo bảng map từ SPEAKER_xx -> NAM_xx / NU_xx dùng AI.
    Ví dụ: {'SPEAKER_00': 'NAM_01', 'SPEAKER_01': 'NU_01'}
    """
    speaker_map = {}
    male_count = 0
    female_count = 0

    # Lấy danh sách các nhãn người nói (labels) từ Pyannote
    labels = diarization_result.labels()

    print(f"🔍 Đang dùng AI phân tích giới tính cho {len(labels)} giọng nói...")

    for label in labels:
        # Lấy timeline các đoạn nói của người này
        timeline = diarization_result.label_timeline(label)

        # Tìm đoạn nói dài nhất của người này để phân tích cho chính xác nhất
        longest_segment = max(timeline, key=lambda s: s.duration)

        # --- GỌI HÀM AI ĐÃ VIẾT Ở TRÊN ---
        gender = get_gender_from_ai(audio_path, longest_segment.start, longest_segment.duration)
        # ---------------------------------

        # Tạo tên mới (NAM_01, NAM_02...)
        if gender == "NAM":
            male_count += 1
            new_label = f"NAM_{male_count:02d}"
        else:
            female_count += 1
            new_label = f"NU_{female_count:02d}"

        speaker_map[label] = new_label
        print(f"   🤖 [AI Gender] {label} -> {gender} -> Gán nhãn: {new_label}")

    return speaker_map

# ==========================================
# API 1: WHISPER (AUDIO -> SRT) - CẬP NHẬT DIARIZATION
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

        # 1. Chạy WHISPER (Transcribe)
        print(f"⏳ [1/2] Đang chạy mô hình Whisper (Medium)...")
        start_time = time.time()
        whisper_result = model.transcribe(input_path, language="zh", fp16=False)
        print(f"✅ Whisper hoàn tất ({time.time() - start_time:.2f}s).")

        # 2. Chạy PYANNOTE (Diarization) - Nếu đã load thành công
        if diarization_pipeline is not None:
            print(f"⏳ [2/2] Đang phân tích người nói (Diarization)...")
            try:
                # Chạy Pyannote để tách người nói
                diarization_result = diarization_pipeline(input_path)

                # --- [MỚI] TẠO MAPPING GIỚI TÍNH ---
                print("🔄 Đang phân tích giới tính bằng AI...")
                speaker_map = create_speaker_mapping(diarization_result, input_path)
                # -----------------------------------

                print("🔄 Đang ghép thông tin vào văn bản...")
                # Truyền thêm speaker_map vào hàm trộn
                whisper_result = align_whisper_with_diarization(whisper_result, diarization_result, speaker_map)
                print("✅ Đã gắn nhãn NAM/NU thành công.")

            except Exception as e_dia:
                print(f"⚠️ CẢNH BÁO: Lỗi khi chạy Diarization/Gender: {e_dia}")
                print("   -> Sẽ xuất SRT mà không có phân biệt người nói.")
        else:
            print("⚠️ Bỏ qua bước phân tích người nói (Do Pipeline chưa khởi tạo).")

        # 3. Xuất SRT
        print(f"💾 Đang lưu file SRT...")
        writer = get_writer("srt", output_dir)
        # writer nhận object result đã được sửa text (thêm [SPEAKER_XX])
        writer(whisper_result, output_name)

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
        # Thêm 5 giây buffer vào cuối để tránh bị cắt cụt
        total_seconds = (subs[-1].end.ordinal / 1000) + 5
        total_samples = int(total_seconds * SAMPLE_RATE)
        final_audio = np.zeros(total_samples, dtype=np.float32)

        print(f"🎙️ Bắt đầu lồng tiếng {len(subs)} câu thoại...")
        req_id = str(uuid.uuid4())[:8]
        count_ok = 0

        for i, sub in enumerate(subs):
            raw_text = sub.text.strip()
            if not raw_text: continue

            start_ms = sub.start.ordinal
            duration_sec = (sub.end.ordinal - sub.start.ordinal) / 1000.0

            # --- LOGIC CHỌN GIỌNG MỚI (CẬP NHẬT) ---

            # 1. Mặc định là giọng Nữ (bao gồm [NU_01], [NU_02] hoặc không có tag)
            voice = VOICE_FEMALE

            text_upper = raw_text.upper()

            # 2. Kiểm tra nếu là giọng Nam
            # Logic: Nếu chứa "[NAM" (khớp với NAM_01, NAM_02, NAM_ANY...) hoặc "[M]"
            if "[NAM" in text_upper or "[M]" in text_upper:
                voice = VOICE_MALE

            # 3. Làm sạch text để đọc (Xóa tag [NAM_01], [NU_02]...)
            # Regex: Tìm chuỗi bắt đầu bằng [, kết thúc bằng ] và thay thế bằng rỗng
            clean_text = re.sub(r"^\[.*?\]", "", raw_text).strip()

            if not clean_text: continue

            # print(f"   🔹 Line {i+1} ({voice}): {clean_text[:30]}...")

            # --- KẾT THÚC LOGIC CHỌN GIỌNG ---

            # Sinh Audio & Ghép
            temp_file = f"temp_{req_id}_{i}.mp3"
            try:
                await generate_tts(clean_text, voice, temp_file)
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