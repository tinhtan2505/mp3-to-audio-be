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
    file_handle.write(f"{index}\n")
    file_handle.write(f"{start} --> {end}\n")
    file_handle.write(f"{text}\n\n")
    file_handle.flush()  # Force ghi ngay


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

        Logger.section("WHISPER - TÁCH LỜI THOẠI (TRUE STREAMING)")
        print(f"   • Đầu vào: {os.path.basename(path)}")
        print(f"   • Chế độ: Streaming real-time (Ghi ngay từng câu)")

        start_w = time.time()

        if WHISPER_BACKEND == "faster":
            # Chuẩn bị file output
            out_dir = os.path.dirname(path)
            base_filename = os.path.splitext(os.path.basename(path))[0].split('_')[0]
            timestamp_str = get_timestamp_str()
            output_files_list = []

            # Biến tracking
            total_segments = 0
            filtered_count = 0
            chunk_index = 1
            current_file_handle = None
            current_file_path = None
            segments_in_current_file = 0

            # Lọc lặp
            prev_texts = []
            repetition_count = 0

            print("\n   ⏳ Bắt đầu xử lý streaming...\n")

            try:
                # Mở file đầu tiên
                part_suffix = f"_part{chunk_index:02d}" if MAX_SEGMENTS_PER_FILE < 9999 else ""
                out_name = f"{base_filename}_cn_{timestamp_str}{part_suffix}.srt"
                current_file_path = os.path.join(out_dir, out_name)
                current_file_handle = open(current_file_path, 'w', encoding='utf-8')
                output_files_list.append(current_file_path)

                print(f"   📝 Đang ghi vào: {out_name}\n")

                # STREAMING TRANSCRIBE - Không chờ hết file
                segments_gen, info = AI_MODELS["whisper"].transcribe(
                    path,
                    language="zh",
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=400),
                    condition_on_previous_text=False,
                    beam_size=1, best_of=1, temperature=0.0,
                    repetition_penalty=1.2, no_speech_threshold=0.6,
                    word_timestamps=True,
                    compression_ratio_threshold=2.0, log_prob_threshold=-1.0,
                    initial_prompt=None
                )

                # ===== XỬ LÝ TỪNG SEGMENT NGAY KHI NHẬN =====
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

                    # Kiểm tra lặp
                    is_repeated = False
                    if text in prev_texts[-2:]:
                        repetition_count += 1
                        if repetition_count >= 2:
                            filtered_count += 1
                            is_repeated = True
                            print(f"   ⚠️  Bỏ qua lặp: '{text[:40]}...'")
                    else:
                        repetition_count = 0

                    # Bỏ qua nếu lặp hoặc quá ngắn
                    if is_repeated or not text or len(text) < 2:
                        continue

                    # Ghi ngay vào file
                    total_segments += 1
                    segments_in_current_file += 1

                    start_ts = format_timestamp(start)
                    end_ts = format_timestamp(end)

                    write_srt_line(
                        current_file_handle,
                        segments_in_current_file,
                        start_ts,
                        end_ts,
                        text
                    )

                    prev_texts.append(text)
                    if len(prev_texts) > 10:
                        prev_texts.pop(0)

                    # Log mỗi 10 câu
                    elapsed = time.time() - start_w
                    print(f"   ✓ [{total_segments:4d}] {text[:50]}... ({elapsed:.1f}s)", flush=True)

                    # Chuyển file mới nếu đủ số câu
                    if segments_in_current_file >= MAX_SEGMENTS_PER_FILE:
                        current_file_handle.close()
                        print(f"\n   ✅ Đã hoàn thành: {out_name} ({segments_in_current_file} câu)")

                        # Mở file mới
                        chunk_index += 1
                        segments_in_current_file = 0

                        part_suffix = f"_part{chunk_index:02d}"
                        out_name = f"{base_filename}_cn_{timestamp_str}{part_suffix}.srt"
                        current_file_path = os.path.join(out_dir, out_name)
                        current_file_handle = open(current_file_path, 'w', encoding='utf-8')
                        output_files_list.append(current_file_path)

                        print(f"   📝 Chuyển sang file mới: {out_name}\n")

                # Đóng file cuối cùng
                if current_file_handle:
                    current_file_handle.close()
                    print(f"\n   ✅ Hoàn thành file cuối: {out_name} ({segments_in_current_file} câu)")

            except KeyboardInterrupt:
                print("\n\n   ⚠️  NGƯỜI DÙNG DỪNG (Ctrl+C)!")
                print(f"   📊 Đã xử lý: {total_segments} câu")

                if current_file_handle:
                    current_file_handle.close()
                    # Đổi tên file sang INTERRUPTED
                    interrupted_path = current_file_path.replace('.srt', '_INTERRUPTED.srt')
                    os.rename(current_file_path, interrupted_path)
                    output_files_list[-1] = interrupted_path
                    print(f"   💾 Đã lưu: {os.path.basename(interrupted_path)}")

                raise HTTPException(499, "Bị dừng bởi người dùng")

            except Exception as e:
                print(f"\n   ❌ LỖI: {e}")
                print(f"   📊 Đã xử lý: {total_segments} câu trước khi lỗi")

                if current_file_handle:
                    current_file_handle.close()
                    # Đổi tên file sang ERROR
                    error_path = current_file_path.replace('.srt', '_ERROR.srt')
                    os.rename(current_file_path, error_path)
                    output_files_list[-1] = error_path
                    print(f"   💾 Đã lưu: {os.path.basename(error_path)}")

                raise

            elapsed = time.time() - start_w

            print(f"\n{'='*60}")
            print(f"📊 THỐNG KÊ CUỐI CÙNG:")
            print(f"   • Ngôn ngữ: {info.language} (Tin cậy: {info.language_probability:.2%})")
            print(f"   • Tổng số câu: {total_segments}")
            print(f"   • Đã lọc lặp: {filtered_count}")
            print(f"   • Số file output: {len(output_files_list)}")
            print(f"   • Thời gian: {elapsed:.2f}s = {elapsed/60:.1f} phút")
            print(f"   • Tốc độ: {total_segments/(elapsed/60):.1f} câu/phút")
            print(f"{'='*60}\n")

            Logger.success(f"Whisper hoàn tất. {len(output_files_list)} files.", elapsed)

            return {
                "status": "success",
                "engine": "faster-whisper",
                "total_segments": total_segments,
                "filtered_segments": filtered_count,
                "split_count": len(output_files_list),
                "output_files": output_files_list,
                "processing_time": elapsed,
                "speed_segments_per_minute": round(total_segments/(elapsed/60), 1)
            }
        else:
            raise HTTPException(400, "Chế độ này chỉ hỗ trợ faster-whisper")

    except HTTPException:
        raise
    except Exception as e:
        Logger.error("Lỗi Whisper", e)
        raise HTTPException(500, str(e))