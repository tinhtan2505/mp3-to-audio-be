import os
import time
import pysrt
import re
import asyncio
import uuid
import librosa
import numpy as np
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from config import VOICE_MALE, VOICE_FEMALE, SAMPLE_RATE
from utils import Logger, get_timestamp_str, generate_tts

router = APIRouter()


class TtsFromViSrtRequest(BaseModel):
    vi_srt_path: str  # Đường dẫn đến file SRT VI


async def generate_tts_with_speedup(text, voice, output_file, available_space, rate="+0%", metadata_cache=None):
    """
    Tạo TTS với tự động tăng tốc nếu audio quá dài

    Args:
        text: Nội dung cần TTS
        voice: Giọng đọc
        output_file: Đường dẫn file MP3 đầu ra
        available_space: Thời gian khả dụng (giây)
        rate: Tốc độ ban đầu
        metadata_cache: Dict chứa metadata đã lưu (để tránh load lại file)

    Returns:
        dict: {
            'success': bool,
            'duration': float,  # Thời lượng audio thực tế
            'speedup_percent': int,  # % tăng tốc đã áp dụng
            'status': str  # ✓ hoặc ⚡X% hoặc ⏭️
            'skipped': bool  # True nếu file đã tồn tại
            'needs_metadata_update': bool  # True nếu cần cập nhật metadata
        }
    """
    # Kiểm tra file đã tồn tại
    if os.path.exists(output_file):
        # Ưu tiên dùng metadata cache nếu có (NHANH)
        if metadata_cache:
            return {
                'success': True,
                'duration': metadata_cache.get('actual_duration', 0),
                'speedup_percent': metadata_cache.get('speedup_percent', 0),
                'status': '⏭️',
                'audio_data': None,  # Không cần load audio
                'skipped': True,
                'needs_metadata_update': False  # Đã có metadata đầy đủ
            }

        # Fallback: load file nếu không có metadata (CHẬM nhưng CẦN THIẾT)
        print(f"      📝 File {os.path.basename(output_file)} thiếu metadata, đang load...")
        try:
            y, _ = librosa.load(output_file, sr=SAMPLE_RATE)
            y_trimmed, _ = librosa.effects.trim(y, top_db=30)
            duration = len(y_trimmed) / SAMPLE_RATE

            return {
                'success': True,
                'duration': duration,
                'speedup_percent': 0,  # Không biết, giả định = 0
                'status': '⏭️',
                'audio_data': y_trimmed,
                'skipped': True,
                'needs_metadata_update': True  # CẦN CẬP NHẬT METADATA
            }
        except Exception as e:
            # Nếu file lỗi thì xóa và tạo lại
            print(f"      ⚠️ File {os.path.basename(output_file)} lỗi, sẽ tạo lại: {str(e)[:50]}")
            try:
                os.remove(output_file)
            except:
                pass

    MAX_SPEED_UP = 60
    tmp_file = f"temp_{uuid.uuid4().hex}.mp3"

    try:
        # Bước 1: TTS với tốc độ ban đầu
        await generate_tts(text, voice, tmp_file, rate=rate)

        # Bước 2: Load và trim audio
        y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
        y_trimmed, _ = librosa.effects.trim(y, top_db=30)
        dur_original = len(y_trimmed) / SAMPLE_RATE

        # Bước 3: Kiểm tra có cần tăng tốc không
        speedup_percent = 0
        status = "✓"

        if dur_original > available_space and available_space >= 0.5:
            # Tính tốc độ cần tăng
            needed_ratio = (dur_original / available_space) - 1.0
            speedup_percent = min(int(needed_ratio * 100) + 5, MAX_SPEED_UP)
            final_rate_str = f"+{speedup_percent}%"

            # TTS lại với tốc độ mới
            os.remove(tmp_file)
            await generate_tts(text, voice, tmp_file, rate=final_rate_str)

            # Load lại audio đã tăng tốc
            y, _ = librosa.load(tmp_file, sr=SAMPLE_RATE)
            y_trimmed, _ = librosa.effects.trim(y, top_db=30)

            status = f"⚡{speedup_percent}%"

        # Bước 4: Lưu file cuối cùng
        final_duration = len(y_trimmed) / SAMPLE_RATE

        # Chuyển sang file đầu ra cuối cùng
        os.rename(tmp_file, output_file)

        return {
            'success': True,
            'duration': final_duration,
            'speedup_percent': speedup_percent,
            'status': status,
            'audio_data': y_trimmed,
            'skipped': False,
            'needs_metadata_update': False  # File mới, sẽ được lưu metadata
        }

    except Exception as e:
        print(f"      ❌ TTS error: {str(e)[:100]}")
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except:
                pass
        return {
            'success': False,
            'duration': 0,
            'speedup_percent': 0,
            'status': '❌',
            'audio_data': None,
            'skipped': False,
            'needs_metadata_update': False
        }


async def tts_batch_processing(vi_srt_path):
    """
    Xử lý TTS cho file VI SRT với LOGIC THỜI GIAN & TĂNG TỐC
    TẠO TỪNG FILE MP3 ĐÃ XỬ LÝ THỜI GIAN, SẴN SÀNG ĐỂ GHÉP

    CẢI TIẾN: Tự động phục hồi metadata cho file đã tồn tại nhưng thiếu metadata
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
        print(f"🎤 BẮT ĐẦU TẠO TTS VỚI XỬ LÝ THỜI GIAN")
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

        # Load metadata cũ TRƯỚC KHI BẮT ĐẦU XỬ LÝ (quan trọng!)
        metadata_file = os.path.join(tts_dir, "timing_metadata.json")
        existing_metadata = {}
        if os.path.exists(metadata_file):
            try:
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    existing_metadata = json.load(f)
                print(f"   📖 Đã load {len(existing_metadata):,} entries từ metadata cũ")
            except Exception as e:
                print(f"   ⚠️ Không thể load metadata cũ: {str(e)[:50]}")

        # Đếm số file đã tồn tại và phân loại
        existing_files = 0
        files_with_metadata = 0
        files_without_metadata = 0

        for sub in subs:
            output_file = os.path.join(tts_dir, f"{sub.index}.mp3")
            if os.path.exists(output_file):
                existing_files += 1
                if str(sub.index) in existing_metadata:
                    files_with_metadata += 1
                else:
                    files_without_metadata += 1

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
        SAFETY_GAP = 0.1  # Khoảng cách an toàn giữa các câu

        print(f"   • Tổng câu: {total_subs:,}")
        print(f"   • Đã tồn tại: {existing_files:,} file MP3")
        print(f"     - ✅ Có metadata: {files_with_metadata:,}")
        print(f"     - 📝 Thiếu metadata: {files_without_metadata:,}")
        print(f"   • Cần tạo mới: {total_subs - existing_files:,} file")
        print(f"   • Batch size: {BATCH_SIZE} câu/lần")
        print(f"   • Số batch: {(total_subs + BATCH_SIZE - 1) // BATCH_SIZE}")
        print(f"   • Max concurrent: {MAX_CONCURRENT_TASKS}")
        print(f"   • Safety gap: {SAFETY_GAP}s")

        remaining = total_subs - existing_files
        if remaining > 0:
            print(f"   • Ước tính thời gian: ~{(remaining / BATCH_SIZE * 0.8):.0f}s ({(remaining / BATCH_SIZE * 0.8 / 60):.1f} phút)")

        if files_without_metadata > 0:
            print(f"   ⚠️  Sẽ tự động phục hồi metadata cho {files_without_metadata:,} file thiếu")

        print()

        processed_count = 0
        success_count = 0
        failed_count = 0
        skipped_count = 0
        speedup_count = 0
        metadata_recovered_count = 0  # Đếm số file được phục hồi metadata
        total_speedup_percent = 0
        tts_files_list = []

        # Dictionary để lưu metadata MỚI của batch hiện tại
        new_metadata = {}

        # Xử lý từng batch
        for batch_start in range(0, total_subs, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_subs)
            batch_subs = subs[batch_start:batch_end]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (total_subs + BATCH_SIZE - 1) // BATCH_SIZE

            print(f"\n📦 Batch {batch_num}/{total_batches} | Câu {batch_start+1}-{batch_end} | Tiến độ: {(batch_end/total_subs*100):.1f}%")

            # Chuẩn bị data cho batch với LOGIC THỜI GIAN
            batch_data = []
            for i, sub in enumerate(batch_subs):
                txt_raw = sub.text.strip()
                clean_txt = re.sub(r"^\[.*?\]", "", txt_raw).strip()
                if not clean_txt:
                    continue

                # Phát hiện giọng nam/nữ
                is_male = "[NAM" in txt_raw.upper() or "[M]" in txt_raw.upper()
                voice = VOICE_MALE if is_male else VOICE_FEMALE

                # Tính toán thời gian
                start_sec = sub.start.ordinal / 1000.0
                end_sec = sub.end.ordinal / 1000.0
                slot_duration = end_sec - start_sec

                # Tính hard_limit (thời gian tối đa có thể dùng)
                global_idx = batch_start + i
                if global_idx < total_subs - 1:
                    next_start = subs[global_idx + 1].start.ordinal / 1000.0
                    hard_limit = next_start - SAFETY_GAP
                else:
                    hard_limit = end_sec + 5.0
                hard_limit = max(hard_limit, end_sec)

                available_space = hard_limit - start_sec

                # Tên file: sử dụng index thực tế từ SRT file
                output_filename = f"{sub.index}.mp3"
                output_path = os.path.join(tts_dir, output_filename)

                batch_data.append({
                    'index': sub.index,
                    'text': clean_txt,
                    'voice': voice,
                    'is_male': is_male,
                    'start_sec': start_sec,
                    'end_sec': end_sec,
                    'slot_duration': slot_duration,
                    'hard_limit': hard_limit,
                    'available_space': available_space,
                    'output_path': output_path
                })

            if not batch_data:
                continue

            # TTS song song với giới hạn concurrent
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)

            async def generate_with_limit(item):
                async with semaphore:
                    try:
                        # Lấy metadata cache nếu có (để tránh load file)
                        idx_str = str(item['index'])
                        metadata_cache = existing_metadata.get(idx_str, None)

                        result = await generate_tts_with_speedup(
                            item['text'],
                            item['voice'],
                            item['output_path'],
                            item['available_space'],
                            rate="+0%",
                            metadata_cache=metadata_cache  # Truyền cache vào
                        )
                        return item['index'], result
                    except Exception as e:
                        return item['index'], {
                            'success': False,
                            'duration': 0,
                            'speedup_percent': 0,
                            'status': '❌',
                            'audio_data': None,
                            'skipped': False,
                            'needs_metadata_update': False
                        }

            # Tạo tasks
            tasks = [generate_with_limit(item) for item in batch_data]

            # Chờ tất cả hoàn thành với timeout
            batch_start_time = time.time()
            try:
                results = await asyncio.wait_for(
                    asyncio.gather(*tasks),
                    timeout=60 * len(batch_data)
                )

                # Xử lý kết quả
                for idx, result in results:
                    processed_count += 1

                    if result['success']:
                        success_count += 1

                        # Đếm số file bị skip
                        if result.get('skipped', False):
                            skipped_count += 1

                            # Đếm số file được phục hồi metadata
                            if result.get('needs_metadata_update', False):
                                metadata_recovered_count += 1

                        # Lưu metadata
                        item = next((x for x in batch_data if x['index'] == idx), None)
                        if item:
                            # LƯU METADATA CHO:
                            # 1. File mới tạo (not skipped)
                            # 2. File cũ nhưng thiếu metadata (needs_metadata_update)
                            if not result.get('skipped', False) or result.get('needs_metadata_update', False):
                                new_metadata[str(idx)] = {
                                    'start_sec': item['start_sec'],
                                    'end_sec': item['end_sec'],
                                    'slot_duration': item['slot_duration'],
                                    'hard_limit': item['hard_limit'],
                                    'available_space': item['available_space'],
                                    'actual_duration': result['duration'],
                                    'speedup_percent': result['speedup_percent'],
                                    'status': result['status'],
                                    'skipped': result.get('skipped', False),
                                    'recovered': result.get('needs_metadata_update', False)  # Đánh dấu là recovered
                                }

                        if result['speedup_percent'] > 0:
                            speedup_count += 1
                            total_speedup_percent += result['speedup_percent']

                        # Thêm vào danh sách kết quả
                        output_file = os.path.join(tts_dir, f"{idx}.mp3")
                        if os.path.exists(output_file):
                            tts_files_list.append(output_file)
                    else:
                        failed_count += 1

            except asyncio.TimeoutError:
                print(f"⚠️ Batch {batch_num} timeout - bỏ qua và tiếp tục")
                failed_count += len(batch_data)
                continue

            batch_tts_time = time.time() - batch_start_time
            print(f"   ⏱️  TTS time: {batch_tts_time:.1f}s | Avg: {batch_tts_time/len(batch_data):.2f}s/câu")
            print(f"   ✓ Thành công: {success_count} | ⏭️ Đã tồn tại: {skipped_count} | ⚡ Tăng tốc: {speedup_count} | ❌ Thất bại: {failed_count}")
            if metadata_recovered_count > 0:
                print(f"   📝 Đã phục hồi metadata: {metadata_recovered_count}")
            print(f"   📊 Đã xử lý: {processed_count}/{total_subs}")

        # Merge metadata mới vào metadata cũ
        existing_metadata.update(new_metadata)

        # Lưu metadata đầy đủ
        try:
            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(existing_metadata, f, indent=2, ensure_ascii=False)
            print(f"\n   💾 Đã lưu {len(existing_metadata):,} entries vào metadata (+ {len(new_metadata):,} mới/cập nhật)")
            if metadata_recovered_count > 0:
                print(f"   ✅ Phục hồi metadata: {metadata_recovered_count:,} file")
        except Exception as e:
            print(f"\n   ⚠️ Không thể lưu metadata: {str(e)}")

        elapsed = time.time() - start_time
        avg_speedup = total_speedup_percent / speedup_count if speedup_count > 0 else 0

        print(f"\n{'='*70}")
        print(f"✅ TTS VỚI XỬ LÝ THỜI GIAN HOÀN TẤT")
        print(f"{'='*70}")
        print(f"   📁 Thư mục TTS: {tts_dir}")
        print(f"   📊 Tổng câu xử lý: {processed_count:,}/{total_subs:,}")
        print(f"   ✓ Thành công: {success_count:,} file MP3")
        print(f"   ⏭️ Đã tồn tại (skip): {skipped_count:,} file")
        print(f"   🆕 Tạo mới: {success_count - skipped_count:,} file")
        print(f"   ⚡ Đã tăng tốc: {speedup_count:,} file (trung bình: {avg_speedup:.1f}%)")
        if metadata_recovered_count > 0:
            print(f"   📝 Phục hồi metadata: {metadata_recovered_count:,} file")
        print(f"   ❌ Thất bại: {failed_count:,} file")
        print(f"   📄 Metadata: {os.path.basename(metadata_file)}")
        print(f"   ⏱️  Thời gian: {elapsed:.1f}s ({elapsed/60:.1f} phút)")
        if processed_count > 0:
            print(f"   ⚡ Tốc độ: {processed_count/elapsed:.1f} file/giây")
        print(f"{'='*70}\n")

        return {
            "status": "success",
            "tts_directory": tts_dir,
            "metadata_file": metadata_file,
            "total_files": total_subs,
            "processed": processed_count,
            "success": success_count,
            "skipped": skipped_count,
            "created": success_count - skipped_count,
            "failed": failed_count,
            "speedup_count": speedup_count,
            "metadata_recovered": metadata_recovered_count,
            "avg_speedup_percent": round(avg_speedup, 1),
            "tts_files": tts_files_list,
            "tts_ready_for_merge": True,
            "processing_time": elapsed
        }

    except Exception as e:
        print(f"\n{'='*70}")
        print(f"❌ LỖI TTS VỚI XỬ LÝ THỜI GIAN")
        print(f"{'='*70}")
        print(f"   🔴 Lỗi: {str(e)}")
        print(f"   📂 File: {os.path.basename(vi_srt_path)}")
        print(f"{'='*70}\n")
        raise


@router.post("/api/v1/dubbing/tts-from-vi-srt")
async def api_tts_from_vi_srt(req: TtsFromViSrtRequest):
    """
    API tạo TTS từ file SRT VI có sẵn với XỬ LÝ THỜI GIAN & TĂNG TỐC

    Request body:
    {
        "vi_srt_path": "/path/to/file_vi.srt"
    }

    Response:
    {
        "status": "success",
        "tts_directory": "/path/to/tts",
        "metadata_file": "/path/to/tts/timing_metadata.json",
        "total_files": 150,
        "processed": 150,
        "success": 148,
        "skipped": 45,
        "created": 103,
        "failed": 2,
        "speedup_count": 45,
        "metadata_recovered": 12,
        "avg_speedup_percent": 23.5,
        "tts_files": ["tts/1.mp3", "tts/2.mp3", ...],
        "tts_ready_for_merge": true,
        "processing_time": 120.5
    }
    """
    try:
        path = os.path.abspath(req.vi_srt_path)

        if not os.path.exists(path):
            raise HTTPException(404, f"File không tồn tại: {path}")

        if not path.endswith('.srt'):
            raise HTTPException(400, "File phải có định dạng .srt")

        Logger.section("TTS TỪ FILE SRT VI VỚI XỬ LÝ THỜI GIAN")
        print(f"   📂 Đầu vào: {os.path.basename(path)} ({os.path.getsize(path) / 1024:.2f} KB)")

        start_time = time.time()

        # Chạy TTS batch processing với logic timing & speedup
        result = await tts_batch_processing(path)

        elapsed = time.time() - start_time
        msg = f"TTS hoàn tất: {result['success']}/{result['total_files']} files"
        if result.get('speedup_count', 0) > 0:
            msg += f" (⚡{result['speedup_count']} speedup)"
        if result.get('skipped', 0) > 0:
            msg += f" (⏭️{result['skipped']} skip)"
        if result.get('metadata_recovered', 0) > 0:
            msg += f" (📝{result['metadata_recovered']} recovered)"

        Logger.success(msg, elapsed)

        return result

    except HTTPException:
        raise
    except Exception as e:
        Logger.error("Lỗi TTS từ VI SRT", e)
        raise HTTPException(500, str(e))