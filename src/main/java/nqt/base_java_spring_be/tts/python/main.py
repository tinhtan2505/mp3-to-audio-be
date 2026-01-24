# main.py
import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager
from utils import Logger, free_port_windows
from ai_core import load_ai_models, check_system_requirements
from config import PORT

# Import các router
from routers import whisper_api, trans_api, tts_api, video_api, video_crop_api, detect_api

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Khởi động hệ thống
    check_system_requirements()
    load_ai_models()
    Logger.section("MÁY CHỦ SẴN SÀNG")
    print(f"📡 API đang chạy tại: http://0.0.0.0:{PORT}")
    print("="*60 + "\n")
    yield
    print("\n👋 Tạm biệt!")

app = FastAPI(lifespan=lifespan)

# Đăng ký Router
app.include_router(whisper_api.router)
app.include_router(trans_api.router)
app.include_router(tts_api.router)
app.include_router(video_api.router)
app.include_router(video_crop_api.router)
app.include_router(detect_api.router)

if __name__ == "__main__":
    free_port_windows(PORT)
    print(f"🚀 KHỞI ĐỘNG SERVER TRÊN CỔNG {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT)