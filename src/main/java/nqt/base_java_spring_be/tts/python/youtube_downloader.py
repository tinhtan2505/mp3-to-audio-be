#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Downloader - Phiên bản cuối cùng
Hỗ trợ đầy đủ các chất lượng video và audio
Yêu cầu: pip install yt-dlp, ffmpeg
"""

import yt_dlp
import os
from pathlib import Path


def get_video_info(url):
    """Lấy thông tin video từ URL"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            return info
        except Exception as e:
            print(f"❌ Lỗi: {e}")
            return None


def get_quality_label(height):
    """Phân loại chất lượng theo độ phân giải"""
    if height >= 2160:
        return "4K"
    elif height >= 1920:
        return "Full HD"  # 1920p = 1080p Full HD
    elif height >= 1440:
        return "2K"
    elif height >= 1280:
        return "Full HD"  # 1280p cũng coi là Full HD
    elif height >= 1080:
        return "Full HD"
    elif height >= 720:
        return "HD"
    elif height >= 480:
        return "Tiêu chuẩn"
    elif height >= 360:
        return "Trung bình ⚡"
    elif height >= 240:
        return "Trung bình"
    elif height >= 144:
        return "Thấp"
    else:
        return "Di động"


def display_formats(info):
    """Hiển thị danh sách định dạng có sẵn"""
    formats = info.get('formats', [])

    video_formats = {}
    audio_formats = {}

    # Lọc VIDEO formats
    for f in formats:
        format_id = f.get('format_id', '')
        ext = f.get('ext', '')
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        height = f.get('height')
        vbr = f.get('vbr', 0)
        tbr = f.get('tbr', 0)
        fps = f.get('fps', 0)

        # Lấy video formats (cả video-only và combined)
        if vcodec != 'none' and height and ext in ['mp4', 'webm']:
            # Ưu tiên: combined stream > video-only, mp4 > webm
            has_audio = acodec != 'none'
            ext_score = 100 if ext == 'mp4' else 0
            quality_score = (vbr or tbr or 0) + (1000 if has_audio else 0) + ext_score

            # Nhóm các độ phân giải tương tự
            # 1920p và 1080p -> cùng nhóm Full HD
            if height >= 1920:
                key = 1920
            elif height >= 1280:
                key = 1280
            elif height >= 1080:
                key = 1080
            else:
                key = height

            if key not in video_formats or quality_score > video_formats[key].get('score', 0):
                label = get_quality_label(height)

                video_formats[key] = {
                    'format_id': format_id,
                    'label': label,
                    'resolution': f"{height}p",
                    'ext': 'mp4',
                    'height': height,
                    'score': quality_score,
                    'has_audio': has_audio,
                    'fps': fps
                }

    # Lọc AUDIO formats
    for f in formats:
        format_id = f.get('format_id', '')
        ext = f.get('ext', '')
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        abr = f.get('abr', 0)

        # Chỉ lấy audio-only streams
        if acodec != 'none' and vcodec == 'none' and ext in ['m4a', 'webm', 'mp4']:
            if abr:
                bitrate = int(abr)

                # Phân loại bitrate
                if bitrate >= 160:
                    key = 'high'
                    label = "Trung bình ⚡"
                elif bitrate >= 100:
                    key = 'medium'
                    label = "Trung bình"
                else:
                    key = 'low'
                    label = "Thấp"

                # Giữ audio có bitrate cao nhất trong mỗi nhóm
                if key not in audio_formats or bitrate > audio_formats[key].get('abr', 0):
                    audio_formats[key] = {
                        'format_id': format_id,
                        'label': label,
                        'bitrate': f"{bitrate}kbps",
                        'ext': 'mp3',
                        'abr': bitrate
                    }

    # Chuyển sang list và sắp xếp
    video_list = sorted(video_formats.values(), key=lambda x: x['height'], reverse=True)
    audio_list = sorted(audio_formats.values(), key=lambda x: x['abr'], reverse=True)

    # Giới hạn audio (lấy 2 mức tốt nhất)
    audio_list = audio_list[:2]

    return video_list, audio_list


def print_menu(video_formats, audio_formats, title):
    """In menu lựa chọn"""
    print("\n" + "="*70)
    print(f"📺 YOUTUBE DOWNLOADER")
    print("="*70)

    # Rút gọn title
    display_title = title if len(title) <= 60 else title[:57] + "..."
    print(f"\n📹 {display_title}")
    print("="*70)

    if video_formats:
        print("\n🎬 VIDEO")
        for i, fmt in enumerate(video_formats, 1):
            audio_note = " (có audio)" if fmt['has_audio'] else ""
            print(f"  {i}. {fmt['label']:<20} {fmt['resolution']:<10} .{fmt['ext']}{audio_note}")

    if audio_formats:
        print("\n🎵 AUDIO")
        audio_start = len(video_formats) + 1
        for i, fmt in enumerate(audio_formats, audio_start):
            print(f"  {i}. {fmt['label']:<20} {fmt['bitrate']:<10} .{fmt['ext']}")

    print("\n  0. Thoát")
    print("="*70)


def get_safe_filename(output_path, base_name, extension):
    """Tạo tên file an toàn, không ghi đè file cũ"""
    filename = f"{base_name}.{extension}"
    filepath = Path(output_path) / filename

    # Nếu file không tồn tại, trả về tên gốc
    if not filepath.exists():
        return filename

    # Nếu file đã tồn tại, tăng số thứ tự
    counter = 1
    while True:
        filename = f"{base_name}_{counter}.{extension}"
        filepath = Path(output_path) / filename
        if not filepath.exists():
            return filename
        counter += 1


def download_media(url, format_id, is_audio=False, output_path="downloads"):
    """Tải xuống media"""
    Path(output_path).mkdir(exist_ok=True)

    # Xác định extension và tên file
    extension = 'mp3' if is_audio else 'mp4'
    safe_filename = get_safe_filename(output_path, 'video_cn', extension)
    output_template = f'{output_path}/{safe_filename}'

    print(f"\n💾 Tên file: {safe_filename}")

    if is_audio:
        # Tải audio và convert sang MP3
        ydl_opts = {
            'format': format_id,
            'outtmpl': output_template,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'progress_hooks': [progress_hook],
        }
    else:
        # Tải video, tự động merge với audio tốt nhất
        ydl_opts = {
            'format': f'{format_id}+bestaudio/best',
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
            'progress_hooks': [progress_hook],
        }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("\n⬇️  Đang tải xuống...")
            ydl.download([url])
            print("\n✅ Tải xuống hoàn tất!")
            print(f"📁 Lưu tại: {os.path.abspath(output_path)}/{safe_filename}")
    except Exception as e:
        print(f"\n❌ Lỗi khi tải: {e}")
        print("💡 Gợi ý: Kiểm tra kết nối mạng hoặc thử video khác")


def progress_hook(d):
    """Hiển thị tiến trình tải"""
    if d['status'] == 'downloading':
        try:
            total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
            downloaded = d.get('downloaded_bytes', 0)
            speed = d.get('speed', 0)
            eta = d.get('eta', 0)

            if total and total > 0:
                percent = (downloaded / total) * 100
                speed_mb = speed / 1024 / 1024 if speed else 0

                if eta:
                    mins, secs = divmod(int(eta), 60)
                    eta_str = f"{mins:02d}:{secs:02d}"
                else:
                    eta_str = "00:00"

                # Hiển thị dung lượng
                downloaded_mb = downloaded / 1024 / 1024
                total_mb = total / 1024 / 1024

                print(f"\r📥 {percent:5.1f}% ({downloaded_mb:.1f}/{total_mb:.1f}MB) | "
                      f"⚡ {speed_mb:5.1f}MB/s | ⏱️  {eta_str}", end='', flush=True)
            else:
                print(f"\r📥 Đang tải...", end='', flush=True)
        except:
            pass

    elif d['status'] == 'finished':
        print("\n🔄 Đang xử lý file...")


def process_video(url):
    """Xử lý tải xuống cho một video"""
    # Lấy thông tin video
    print("\n⏳ Đang tải thông tin...")
    info = get_video_info(url)

    if not info:
        return False

    title = info.get('title', 'Unknown')
    duration = info.get('duration', 0)

    # Hiển thị thông tin video
    if duration:
        mins = duration // 60
        secs = duration % 60
        print(f"⏱️  Thời lượng: {mins}:{secs:02d}")

    # Lấy danh sách formats
    video_formats, audio_formats = display_formats(info)

    if not video_formats and not audio_formats:
        print("❌ Không tìm thấy format phù hợp!")
        return False

    # Vòng lặp tải nhiều định dạng từ cùng video
    while True:
        # Hiển thị menu
        print_menu(video_formats, audio_formats, title)

        # Lựa chọn
        try:
            total_options = len(video_formats) + len(audio_formats)
            choice_str = input(f"\n👉 Chọn định dạng (0-{total_options}), hoặc nhập 'n' để chuyển video khác: ").strip().lower()

            # Kiểm tra xem có muốn chuyển video không
            if choice_str == 'n':
                return True  # Trả về True để tiếp tục với video mới

            # Kiểm tra nếu nhập 0
            if choice_str == '0':
                print("\n👋 Tạm biệt!")
                return False  # Kết thúc chương trình

            # Chuyển đổi sang số
            choice = int(choice_str)

            if 1 <= choice <= len(video_formats):
                # Tải video
                selected = video_formats[choice - 1]
                print(f"\n📌 Đã chọn: {selected['label']} {selected['resolution']}")

                if not selected['has_audio']:
                    print("💡 Video này sẽ được merge với audio tốt nhất")

                download_media(url, selected['format_id'], is_audio=False)

            elif len(video_formats) < choice <= total_options:
                # Tải audio
                audio_index = choice - len(video_formats) - 1
                selected = audio_formats[audio_index]
                print(f"\n📌 Đã chọn: Audio {selected['bitrate']}")
                download_media(url, selected['format_id'], is_audio=True)

            else:
                print("❌ Lựa chọn không hợp lệ!")
                continue

            # Hỏi có muốn tải thêm định dạng khác không
            print("\n" + "-"*70)
            next_action = input("💾 Bạn muốn: [1] Tải định dạng khác, [2] Chuyển video mới, [0] Thoát: ").strip()

            if next_action == '0':
                print("\n👋 Tạm biệt!")
                return False
            elif next_action == '2':
                return True  # Chuyển video mới
            # Nếu chọn 1 hoặc enter, tiếp tục vòng lặp

        except ValueError:
            print("❌ Vui lòng nhập số hoặc 'n'!")
        except KeyboardInterrupt:
            print("\n\n👋 Đã hủy!")
            return False
        except Exception as e:
            print(f"\n❌ Lỗi: {e}")


def main():
    """Hàm chính"""
    print("\n" + "="*70)
    print("🎬 YOUTUBE DOWNLOADER")
    print("Tải video chất lượng cao: Full HD (1080p), HD (720p), 4K")
    print("Tải audio MP3: 128kbps - 192kbps")
    print("="*70)

    # Vòng lặp chính cho nhiều video
    while True:
        # Nhập URL
        url = input("\n🔎 Nhập link YouTube (hoặc '0' để thoát): ").strip()

        if url == '0':
            print("\n👋 Tạm biệt!")
            break

        if not url:
            print("❌ Vui lòng nhập URL!")
            continue

        # Xử lý video
        continue_program = process_video(url)

        if not continue_program:
            break  # Kết thúc chương trình


if __name__ == "__main__":
    main()