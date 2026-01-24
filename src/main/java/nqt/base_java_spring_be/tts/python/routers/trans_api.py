import os
import time
import pysrt
from datetime import datetime
from fastapi import APIRouter, HTTPException
from schemas import TranslateRequest
from ai_core import (
    AI_MODELS,
    process_batch_recursive,
    process_batch_recursive_ollama,
    call_gemini_fix_lines,
    is_valid_translation
)
from config import TRANS_BATCH_SIZE
from utils import Logger

router = APIRouter()

# --- 5.2. API DỊCH THUẬT (GEMINI) ---
@router.post("/api/v1/dubbing/translate")
def api_translate_gemini(req: TranslateRequest):
    start_time = time.time()
    Logger.section("DỊCH THUẬT GEMINI")
    print(f"   • Đầu vào: {req.input_srt_path}")

    # SỬA: Đổi từ "gemini_model" sang "gemini_client"
    if not AI_MODELS["gemini_client"]:
        raise HTTPException(500, "Gemini chưa được cấu hình Key!")

    try:
        input_path = os.path.abspath(req.input_srt_path)
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"Không tìm thấy: {input_path}")

        dir_name, base_name = os.path.split(input_path)
        output_path = os.path.join(dir_name, f"{os.path.splitext(base_name)[0]}_vi_TienHiep.srt")

        try:
            subs = pysrt.open(input_path)
        except:
            subs = pysrt.open(input_path, encoding='utf-8')

        total_subs = len(subs)
        print(f"   📚 Tổng số dòng thoại: {total_subs}")

        for i in range(0, total_subs, TRANS_BATCH_SIZE):
            batch_start = time.time()
            current_batch = subs[i : i + TRANS_BATCH_SIZE]
            translated_texts = process_batch_recursive(current_batch, i)

            print(f"\n--- LÔ: {min(i + TRANS_BATCH_SIZE, total_subs)}/{total_subs} | ⏳ {time.time() - batch_start:.2f}s ---")
            for j, new_text in enumerate(translated_texts):
                if i + j >= total_subs: break
                sub_item = subs[i+j]
                print(f"#{sub_item.index}: {sub_item.text} -> {new_text}")
                sub_item.text = new_text

            print(f"   💾 Đang lưu tạm...")
            subs.save(output_path, encoding='utf-8')

        elapsed = time.time() - start_time
        Logger.success("DỊCH GEMINI HOÀN TẤT", elapsed)
        return {"status": "success", "output_file": output_path, "total_lines": total_subs}

    except Exception as e:
        Logger.error("Lỗi Dịch Gemini", e)
        raise HTTPException(500, str(e))