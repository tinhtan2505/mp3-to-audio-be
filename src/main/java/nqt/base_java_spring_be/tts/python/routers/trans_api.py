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
@router.post("/api/v1/dubbing/translate-gemini")
def api_translate_gemini(req: TranslateRequest):
    start_time = time.time()
    Logger.section("DỊCH THUẬT GEMINI")
    print(f"   • Đầu vào: {req.input_srt_path}")

    if not AI_MODELS["gemini_model"]:
        raise HTTPException(500, "Gemini chưa được cấu hình Key!")

    try:
        input_path = os.path.abspath(req.input_srt_path)
        if not os.path.exists(input_path): raise FileNotFoundError(f"Không tìm thấy: {input_path}")

        dir_name, base_name = os.path.split(input_path)
        output_path = os.path.join(dir_name, f"{os.path.splitext(base_name)[0]}_vi_TienHiep.srt")

        try: subs = pysrt.open(input_path)
        except: subs = pysrt.open(input_path, encoding='utf-8')
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

# --- 5.3. API DỊCH THUẬT (OLLAMA + GEMINI FIX) ---
@router.post("/api/v1/dubbing/translate")
def api_translate(req: TranslateRequest):
    # LOG THỜI GIAN BẮT ĐẦU
    start_time = time.time()
    start_str = datetime.now().strftime("%H:%M:%S")

    Logger.section("DỊCH THUẬT: OLLAMA -> GEMINI -> GOOGLE")
    print(f"⏰ Thời gian bắt đầu: {start_str}")
    print(f"   • File: {req.input_srt_path}")

    if not AI_MODELS["ollama_client"]: raise HTTPException(500, "Ollama chưa kết nối")

    try:
        path = os.path.abspath(req.input_srt_path)
        out_path = path.replace(".srt", "_vi_Final.srt")
        try: subs = pysrt.open(path)
        except: subs = pysrt.open(path, encoding='utf-8')

        total_subs = len(subs)
        failed_lines_map = {} # Map các dòng lỗi cần cứu

        print(f"   📚 Tổng số dòng: {total_subs}")

        # --- GIAI ĐOẠN 1: OLLAMA ---
        print("\n" + "-"*40)
        print("🏁 BƯỚC 1: DỊCH THÔ (OLLAMA)")
        print("-"*40)

        for i in range(0, total_subs, TRANS_BATCH_SIZE):
            batch_start = time.time()
            current_batch = subs[i : i + TRANS_BATCH_SIZE]

            # Gọi hàm xử lý và in log chi tiết bên trong
            translated_texts = process_batch_recursive_ollama(current_batch, i)

            # Cập nhật Text & Check Lỗi để gom lại
            for j, new_text in enumerate(translated_texts):
                idx = i + j
                sub_item = subs[idx]
                orig_cn = sub_item.text

                sub_item.text = new_text

                # Check lại lần nữa để đưa vào list Failed
                is_ok, _ = is_valid_translation(new_text)
                if not is_ok:
                    failed_lines_map[idx] = orig_cn
                    sub_item.text = f"[CHỜ FIX] {orig_cn}"

            # LOG THỜI GIAN BATCH
            batch_dur = time.time() - batch_start
            print(f"⏱️  [Batch {i}-{min(i+20, total_subs)}] Hoàn thành trong {batch_dur:.2f}s")

            # Lưu tạm
            subs.save(out_path, encoding='utf-8')

        # --- GIAI ĐOẠN 2: CỨU HỘ (GEMINI -> GOOGLE) ---
        if failed_lines_map:
            fixed_map = call_gemini_fix_lines(failed_lines_map)

            success_count = 0
            for idx, fixed_text in fixed_map.items():
                if idx < len(subs):
                    subs[idx].text = fixed_text
                    success_count += 1
            print(f"\n   ✅ Đã sửa: {success_count}/{len(failed_lines_map)} dòng lỗi.")
        else:
            print("\n✨ Tuyệt vời! Ollama không gặp lỗi nào.")

        subs.save(out_path, encoding='utf-8')

        # LOG TỔNG KẾT
        total_elapsed = time.time() - start_time
        print("\n" + "="*60)
        print(f"🎉 DỊCH HOÀN TẤT!")
        print(f"⏰ Bắt đầu: {start_str} | Kết thúc: {datetime.now().strftime('%H:%M:%S')}")
        print(f"⏱️  Tổng thời gian: {total_elapsed:.2f} giây")
        print(f"💾 File ra: {out_path}")
        print("="*60)

        return {"status": "success", "output_file": out_path, "elapsed": total_elapsed}

    except Exception as e:
        Logger.error("Lỗi Dịch", e)
        raise HTTPException(500, str(e))