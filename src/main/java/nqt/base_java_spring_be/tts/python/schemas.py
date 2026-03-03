# schemas.py
from pydantic import BaseModel
from typing import Optional

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
    branding_text: str = "Thúy Lụa Drama Review"
    branding_image_path: Optional[str] = "D:/Dubbing/logo.png"
    subtitle_path: Optional[str] = None          # Đường dẫn file .srt vietsub
    subtitle_font_size: int = 28                 # Cỡ chữ subtitle
    subtitle_font_color: str = "white"           # Màu chữ
    subtitle_border_color: str = "black"         # Màu viền chữ
    subtitle_border_width: int = 2               # Độ dày viền
    watermark_lines: bool = False