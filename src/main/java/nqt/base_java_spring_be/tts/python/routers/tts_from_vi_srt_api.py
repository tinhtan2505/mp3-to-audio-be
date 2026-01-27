import os
import time
import pysrt
import re
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VOICE_MALE, VOICE_FEMALE
from utils import Logger, get_timestamp_str, generate_tts

router = APIRouter()


class TtsFromViSrtRequest(BaseModel):
    vi_srt_path: str  # Đường dẫn đến file SRT VI


async def generate_tts_internal(text, voice, output_file, rate="+0%"):
    """
    Hàm TTS nội bộ - wrapper cho generate_tts từ utils
    """
    try:
        await generate_tts(text, voice, output_file, rate)
        return True
    except Exception as e:
        print(f"      ❌ TTS error: {str(e)[:50]}")
        return False


async def tts_batch_processing(vi_srt_path):
    """
    Xử lý TTS cho file VI SRT
    TẠO TỪNG FILE MP3 RIÊNG BIỆT CHO MỖI SUBTITLE
    """
    start_time = time.time()

    try:
        # Kiểm tra file tồn tại
        if not os.path.exists(vi_srt_path):
            raise FileNotFoundError(f"File không tồn tại: {vi_srt_path}")

        # Tạo thư mục tts
        srt_dir = os.path.dirname(vi_srt_path)
        tts_dir = os.path.join(srt_dir, "tts")
        os.makedirs(tts_dir, exist_ok=True)

        print(f"\n{'='*70}")
        print(f"🎤 BẮT ĐẦU TẠO TTS TỪ FILE SRT VI")
        print(f"{'='*70}")
        print(f"   📂 File VI: {os.path.basename(vi_srt_path)}")
        print(f"   📁 Thư mục TTS: {tts_dir}")

        # Đọc file SRT
        try:
            subs = pysrt.open(vi_srt_path)
        except:
            subs = pysrt.open(vi_srt_path, encoding='utf-8')

        if not subs:
            raise ValueError("File SRT rỗng")

        total_subs = len(subs)

        # Tự động tính BATCH_SIZE
        if total_subs < 100:
            BATCH_SIZE = 20
        elif total_subs < 500:
            BATCH_SIZE = 30
        elif total_subs < 1000:
            BATCH_SIZE = 40
        elif total_subs < 3000:
            BATCH_SIZE = 50
        else:
            BATCH_SIZE = 60

        MAX_CONCURRENT_TASKS = 50

        print(f"   • Tổng câu: {total_subs:,}")
        print(f"   • Batch size: {BATCH_SIZE} câu/lần")
        print(f"   • Số batch: {(total_subs + BATCH_SIZE - 1) // BATCH_SIZE}")
        print(f"   • Max concurrent: {MAX_CONCURRENT_TASKS}")
        print(f"   • Ước tính thời gian: ~{(total_subs / BATCH_SIZE * 0.8):.0f}s ({(total_subs / BATCH_SIZE * 0.8 / 60):.1f} phút)\n")

        processed_count = 0
        success_count = 0
        failed_count = 0
        tts_files_list = []

        # Xử lý từng batch
        for batch_start in range(0, total_subs, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_subs)
            batch_subs = subs[batch_start:batch_end]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (total_subs + BATCH_SIZE - 1) // BATCH_SIZE

            print(f"\n📦 Batch {batch_num}/{total_batches} | Câu {batch_start+1}-{batch_end} | Tiến độ: {(batch_end/total_subs*100):.1f}%")

            # Chuẩn bị data cho batch
            batch_data = []
            for i, sub in enumerate(batch_subs):
                txt_raw = sub.text.strip()
                clean_txt = re.sub(r"^\[.*?\]", "", txt_raw).strip()
                if not clean_txt:
                    continue

                # Phát hiện giọng nam/nữ
                is_male = "[NAM" in txt_raw.upper() or "[M]" in txt_raw.upper()
                voice = VOICE_MALE if is_male else VOICE_FEMALE

                # Tên file: sử dụng index thực tế từ SRT file
                output_filename = f"{sub.index}.mp3"
                output_path = os.path.join(tts_dir, output_filename)

                batch_data.append({
                    'index': sub.index,  # Sử dụng index thực tế từ SRT
                    'text': clean_txt,
                    'voice': voice,
                    'output_path': output_path
                })

            if not batch_data:
                continue

            # TTS song song với giới hạn concurrent
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

            async def generate_with_limit(item):
                async with semaphore:
                    try:
                        success = await generate_tts_internal(
                            item['text'],
                            item['voice'],
                            item['output_path'],
                            rate="+0%"
                        )
                        return item['index'], item['output_path'], success
                    except Exception as e:
                        return item['index'], item['output_path'], False

            # Tạo tasks
            tasks = [generate_with_limit(item) for item in batch_data]

            # Chờ tất cả hoàn thành với timeout
            batch_start_time = time.time()
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=60 * len(batch_data)
                )

                # Đếm thành công/thất bại
                for idx, output_path, success in results:
                    processed_count += 1
                    if success and os.path.exists(output_path):
                        success_count += 1
                        tts_files_list.append(output_path)
                    else:
                        failed_count += 1

            except asyncio.TimeoutError:
                print(f"⚠️ Batch {batch_num} timeout - bỏ qua và tiếp tục")
                failed_count += len(batch_data)
                continue

            batch_tts_time = time.time() - batch_start_time
            print(f"   ⏱️ TTS time: {batch_tts_time:.1f}s | Avg: {batch_tts_time/len(batch_data):.2f}s/câu")
            print(f"   ✓ Thành công: {success_count} | ❌ Thất bại: {failed_count} | 📊 Đã xử lý: {processed_count}/{total_subs}")

        elapsed = time.time() - start_time

        print(f"\n{'='*70}")
        print(f"✅ TTS HOÀN TẤT")
        print(f"{'='*70}")
        print(f"   📁 Thư mục TTS: {tts_dir}")
        print(f"   📊 Tổng câu xử lý: {processed_count:,}/{total_subs:,}")
        print(f"   ✓ Thành công: {success_count:,} file MP3")
        print(f"   ❌ Thất bại: {failed_count:,} file")
        print(f"   ⏱️  Thời gian: {elapsed:.1f}s ({elapsed/60:.1f} phút)")
        if processed_count > 0:
            print(f"   ⚡ Tốc độ: {processed_count/elapsed:.1f} file/giây")
        print(f"{'='*70}\n")

        return {
            "status": "success",
            "tts_directory": tts_dir,
            "total_files": total_subs,
            "processed": processed_count,
            "success": success_count,
            "failed": failed_count,
            "tts_files": tts_files_list,
            "processing_time": elapsed
        }

    except Exception as e:
        print(f"\n{'='*70}")
        print(f"❌ LỖI TTS CHO FILE VI")
        print(f"{'='*70}")
        print(f"   🔴 Lỗi: {str(e)}")
        print(f"   📂 File: {os.path.basename(vi_srt_path)}")
        print(f"{'='*70}\n")
        raise


@router.post("/api/v1/dubbing/tts-from-vi-srt")
async def api_tts_from_vi_srt(req: TtsFromViSrtRequest):
    """
    API tạo TTS từ file SRT VI có sẵn

    Request body:
    {
        "vi_srt_path": "/path/to/file_vi.srt"
    }

    Response:
    {
        "status": "success",
        "tts_directory": "/path/to/tts",
        "total_files": 150,
        "processed": 150,
        "success": 148,
        "failed": 2,
        "tts_files": ["tts/1.mp3", "tts/2.mp3", ...],
        "processing_time": 120.5
    }
    """
    try:
        path = os.path.abspath(req.vi_srt_path)

        if not os.path.exists(path):
            raise HTTPException(404, f"File không tồn tại: {path}")

        if not path.endswith('.srt'):
            raise HTTPException(400, "File phải có định dạng .srt")

        Logger.section("TTS TỪ FILE SRT VI")
        print(f"   📂 Đầu vào: {os.path.basename(path)} ({os.path.getsize(path) / 1024:.2f} KB)")

        start_time = time.time()

        # Chạy TTS batch processing
        result = await tts_batch_processing(path)

        elapsed = time.time() - start_time
        Logger.success(f"TTS hoàn tất: {result['success']}/{result['total_files']} files", elapsed)

        return result

    except HTTPException:
        raise
    except Exception as e:
        Logger.error("Lỗi TTS từ VI SRT", e)
        raise HTTPException(500, str(e))