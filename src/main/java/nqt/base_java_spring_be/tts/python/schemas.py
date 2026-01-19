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
    branding_text: str = "NQT DRAMA REVIEW"
    branding_image_path: Optional[str] = "D:/Dubbing/logo.png"