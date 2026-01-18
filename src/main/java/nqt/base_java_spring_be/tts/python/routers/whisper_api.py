import os
import time
from fastapi import APIRouter, HTTPException
from schemas import WhisperRequest
from ai_core import AI_MODELS
from config import WHISPER_BACKEND, MAX_SEGMENTS_PER_FILE
from utils import Logger, get_timestamp_str, normalize_segment_time

router = APIRouter()


def write_srt_line(file_handle, index, start, end, text):
    """Ghi 1 dòng SRT vào file ngay lập tức"""
    file_handle.write(f"{index}\n{start} --> {end}\n{text}\n\n")
    file_handle.flush()


def format_timestamp(seconds):
    """Chuyển seconds thành format SRT: 00:00:00,000"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


@router.post("/api/v1/dubbing/whisper")
def api_whisper(req: WhisperRequest):
    if not AI_MODELS["whisper"]:
        raise HTTPException(500, "Model Whisper chưa được tải")

    try:
        path = os.path.abspath(req.input_path)
        if not os.path.exists(path):
            raise FileNotFoundError(f"File không tồn tại: {path}")

        Logger.section("WHISPER - TÁCH LỜI THOẠI (TỐI ƯU STREAMING)")
        print(f"   📂 Đầu vào: {os.path.basename(path)} ({os.path.getsize(path) / (1024*1024):.2f} MB)")
        print(f"   ⚙️  Chế độ: Streaming thời gian thực + Tự động chia ({MAX_SEGMENTS_PER_FILE} câu/file)")
        print(f"   🔧 Engine: {WHISPER_BACKEND}")

        start_w = time.time()

        if WHISPER_BACKEND == "faster":
            # Chuẩn bị output
            out_dir = os.path.dirname(path)
            base_filename = os.path.splitext(os.path.basename(path))[0].split('_')[0]
            timestamp_str = get_timestamp_str()
            output_files_list = []

            # Tracking variables
            total_segments = 0
            filtered_count = 0
            chunk_index = 1
            current_file_handle = None
            current_file_path = None
            segments_in_current_file = 0

            # Tối ưu: sử dụng set cho lookup O(1)
            seen_texts = set()
            last_texts = []  # Queue nhỏ để check lặp gần

            # Thống kê chi tiết
            stats = {
                'total_duration': 0,
                'total_chars': 0,
                'empty_segments': 0,
                'short_segments': 0,
                'duplicates': 0,
                'files_created': 0,
                'avg_segment_length': 0,
                'last_log_time': start_w,
                'segments_since_log': 0
            }

            print(f"\n{'='*70}")
            print(f"🚀 BẮT ĐẦU CHUYỂN ÂM THANH THÀNH VĂN BẢN...")
            print(f"{'='*70}\n")

            try:
                # Mở file đầu tiên
                part_suffix = f"_part{chunk_index:02d}" if MAX_SEGMENTS_PER_FILE < 9999 else ""
                out_name = f"{base_filename}_cn_{timestamp_str}{part_suffix}.srt"
                current_file_path = os.path.join(out_dir, out_name)
                current_file_handle = open(current_file_path, 'w', encoding='utf-8', buffering=8192)
                output_files_list.append(current_file_path)
                stats['files_created'] = 1

                print(f"   📝 File số {stats['files_created']}: {out_name}")
                print(f"   ⏱️  Thời gian bắt đầu: {time.strftime('%H:%M:%S')}\n")

                # STREAMING TRANSCRIBE
                init_time = time.time()
                segments_gen, info = AI_MODELS["whisper"].transcribe(
                    path,
                    language="zh",
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=400),
                    condition_on_previous_text=False,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    repetition_penalty=1.2,
                    no_speech_threshold=0.6,
                    word_timestamps=True,
                    compression_ratio_threshold=2.0,
                    log_prob_threshold=-1.0,
                    initial_prompt=None
                )

                init_elapsed = time.time() - init_time
                print(f"   ✓ Khởi tạo model: {init_elapsed:.2f}s")
                print(f"   🌐 Ngôn ngữ: {info.language} (độ tin cậy: {info.language_probability:.2%})")
                print(f"   ⏳ Đang xử lý các đoạn văn bản...\n")

                # XỬ LÝ STREAMING
                for seg in segments_gen:
                    # Normalize segment
                    if hasattr(seg, 'start'):
                        start = seg.start
                        end = seg.end
                        text = seg.text.strip()
                    else:
                        normalized = normalize_segment_time(seg)
                        start = normalized['start']
                        end = normalized['end']
                        text = normalized['text'].strip()

                    duration = end - start
                    stats['total_duration'] += duration

                    # Kiểm tra empty
                    if not text:
                        stats['empty_segments'] += 1
                        continue

                    # Kiểm tra quá ngắn
                    if len(text) < 2:
                        stats['short_segments'] += 1
                        continue

                    # Tối ưu: check duplicate bằng set (O(1))
                    text_normalized = text.lower().strip()
                    if text_normalized in seen_texts or text in last_texts[-3:]:
                        stats['duplicates'] += 1
                        filtered_count += 1
                        if stats['duplicates'] % 5 == 0:  # Log mỗi 5 duplicate
                            print(f"   ⚠️  Đã lọc {stats['duplicates']} câu trùng lặp...")
                        continue

                    # Ghi segment hợp lệ
                    total_segments += 1
                    segments_in_current_file += 1
                    stats['total_chars'] += len(text)
                    stats['segments_since_log'] += 1

                    # Cập nhật tracking
                    seen_texts.add(text_normalized)
                    last_texts.append(text)
                    if len(last_texts) > 5:  # Giữ queue nhỏ
                        last_texts.pop(0)

                    # Ghi file với index nối tiếp toàn cục
                    start_ts = format_timestamp(start)
                    end_ts = format_timestamp(end)
                    write_srt_line(
                        current_file_handle,
                        total_segments,  # ← Thay đổi: dùng total_segments thay vì segments_in_current_file
                        start_ts,
                        end_ts,
                        text
                    )

                    # Log định kỳ (mỗi 3 giây hoặc 20 segments)
                    now = time.time()
                    if (now - stats['last_log_time'] >= 3.0) or (stats['segments_since_log'] >= 20):
                        elapsed = now - start_w
                        speed = total_segments / (elapsed / 60) if elapsed > 0 else 0
                        progress_pct = (stats['total_duration'] / (elapsed or 1)) * 100

                        print(f"   ⚡ [{total_segments:4d}] {text[:45]}{'...' if len(text)>45 else ''}")
                        print(f"      └─ Tốc độ: {speed:.1f} câu/phút | Thời gian: {elapsed:.1f}s | Tiến độ: ~{min(progress_pct, 99):.0f}%")

                        stats['last_log_time'] = now
                        stats['segments_since_log'] = 0

                    # Auto-split file
                    if segments_in_current_file >= MAX_SEGMENTS_PER_FILE:
                        current_file_handle.close()
                        elapsed = time.time() - start_w

                        print(f"\n   ✅ File số {stats['files_created']} hoàn thành: {segments_in_current_file} câu")
                        print(f"      └─ Đường dẫn: {os.path.basename(current_file_path)}")
                        print(f"      └─ Thời gian: {elapsed:.1f}s\n")

                        # Mở file mới
                        chunk_index += 1
                        segments_in_current_file = 0  # ← Reset counter cho file mới
                        stats['files_created'] += 1

                        part_suffix = f"_part{chunk_index:02d}"
                        out_name = f"{base_filename}_cn_{timestamp_str}{part_suffix}.srt"
                        current_file_path = os.path.join(out_dir, out_name)
                        current_file_handle = open(current_file_path, 'w', encoding='utf-8', buffering=8192)
                        output_files_list.append(current_file_path)

                        print(f"   📝 File số {stats['files_created']}: {out_name}\n")

                # Đóng file cuối
                if current_file_handle:
                    current_file_handle.close()
                    print(f"\n   ✅ File cuối cùng hoàn thành: {segments_in_current_file} câu")

            except KeyboardInterrupt:
                print(f"\n\n{'='*70}")
                print(f"⚠️  BỊ NGẮT BỞI NGƯỜI DÙNG (Ctrl+C)")
                print(f"{'='*70}")
                print(f"   📊 Đã xử lý: {total_segments} câu trước khi bị ngắt")
                print(f"   ⏱️  Thời gian: {time.time() - start_w:.1f}s")

                if current_file_handle:
                    current_file_handle.close()
                    interrupted_path = current_file_path.replace('.srt', '_INTERRUPTED.srt')
                    os.rename(current_file_path, interrupted_path)
                    output_files_list[-1] = interrupted_path
                    print(f"   💾 Đã lưu file tạm: {os.path.basename(interrupted_path)}\n")

                raise HTTPException(499, "Interrupted by user")

            except Exception as e:
                print(f"\n\n{'='*70}")
                print(f"❌ XẢY RA LỖI")
                print(f"{'='*70}")
                print(f"   🔴 Lỗi: {str(e)}")
                print(f"   📊 Đã xử lý: {total_segments} câu trước khi lỗi")

                if current_file_handle:
                    current_file_handle.close()
                    error_path = current_file_path.replace('.srt', '_ERROR.srt')
                    os.rename(current_file_path, error_path)
                    output_files_list[-1] = error_path
                    print(f"   💾 Đã lưu file tạm: {os.path.basename(error_path)}\n")

                raise

            elapsed = time.time() - start_w
            stats['avg_segment_length'] = stats['total_chars'] / total_segments if total_segments > 0 else 0

            # Final report
            print(f"\n{'='*70}")
            print(f"✅ CHUYỂN ÂM THANH THÀNH VĂN BẢN HOÀN TẤT")
            print(f"{'='*70}")
            print(f"📊 THỐNG KÊ:")
            print(f"   • Ngôn ngữ: {info.language} (độ tin cậy: {info.language_probability:.2%})")
            print(f"   • Tổng số câu: {total_segments:,}")
            print(f"   • Câu hợp lệ: {total_segments:,}")
            print(f"   • Đã lọc bỏ:")
            print(f"      - Trùng lặp: {stats['duplicates']}")
            print(f"      - Rỗng: {stats['empty_segments']}")
            print(f"      - Quá ngắn: {stats['short_segments']}")
            print(f"      - Tổng cộng: {filtered_count}")
            print(f"   • Số file tạo ra: {len(output_files_list)}")
            print(f"   • Độ dài trung bình: {stats['avg_segment_length']:.1f} ký tự/câu")
            print(f"\n⏱️  HIỆU SUẤT:")
            print(f"   • Tổng thời gian: {elapsed:.2f}s ({elapsed/60:.1f} phút)")
            print(f"   • Tốc độ xử lý: {total_segments/(elapsed/60):.1f} câu/phút")
            print(f"   • Thời lượng audio: ~{stats['total_duration']:.1f}s")
            print(f"   • Hệ số thời gian thực: {stats['total_duration']/elapsed:.2f}x")
            print(f"\n📁 CÁC FILE ĐẦU RA:")
            for i, f in enumerate(output_files_list, 1):
                print(f"   {i}. {os.path.basename(f)}")
            print(f"{'='*70}\n")

            Logger.success(f"Whisper hoàn tất: {len(output_files_list)} files, {total_segments} câu", elapsed)

            return {
                "status": "success",
                "engine": "faster-whisper",
                "total_segments": total_segments,
                "filtered_segments": filtered_count,
                "split_count": len(output_files_list),
                "output_files": output_files_list,
                "processing_time": elapsed,
                "speed_segments_per_minute": round(total_segments/(elapsed/60), 1),
                "statistics": {
                    "duplicates": stats['duplicates'],
                    "empty": stats['empty_segments'],
                    "short": stats['short_segments'],
                    "avg_length": round(stats['avg_segment_length'], 1),
                    "audio_duration": round(stats['total_duration'], 1),
                    "realtime_factor": round(stats['total_duration']/elapsed, 2)
                }
            }
        else:
            raise HTTPException(400, "Chế độ này chỉ hỗ trợ faster-whisper")

    except HTTPException:
        raise
    except Exception as e:
        Logger.error("Lỗi Whisper", e)
        raise HTTPException(500, str(e))