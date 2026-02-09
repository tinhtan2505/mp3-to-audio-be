#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YouTube Downloader - Phiên bản cải tiến
Tải video không tiếng (mp4) + audio chất lượng cao (AAC/M4A) cho faster_whisper
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
        return "Full HD"
    elif height >= 1440:
        return "2K"
    elif height >= 1280:
        return "Full HD"
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

    # Lọc VIDEO formats (chỉ video-only, không có audio)
    for f in formats:
        format_id = f.get('format_id', '')
        ext = f.get('ext', '')
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        height = f.get('height')
        vbr = f.get('vbr', 0)
        tbr = f.get('tbr', 0)
        fps = f.get('fps', 0)

        # Lấy video formats (CHỈ video-only, không có audio)
        if vcodec != 'none' and acodec == 'none' and height and ext in ['mp4', 'webm']:
            # Ưu tiên: mp4 > webm
            ext_score = 100 if ext == 'mp4' else 0
            quality_score = (vbr or tbr or 0) + ext_score

            # Nhóm các độ phân giải tương tự
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
                    'fps': fps
                }

    # Lọc AUDIO formats - Ưu tiên AAC chất lượng cao
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

                # Ưu tiên AAC
                is_aac = 'aac' in acodec.lower() if acodec else False
                codec_score = 100 if is_aac else 0

                # Phân loại bitrate
                if bitrate >= 160:
                    key = 'high'
                    label = "Cao ⚡"
                elif bitrate >= 100:
                    key = 'medium'
                    label = "Trung bình"
                else:
                    key = 'low'
                    label = "Thấp"

                # Giữ audio có bitrate cao nhất + ưu tiên AAC trong mỗi nhóm
                quality_score = bitrate + codec_score
                if key not in audio_formats or quality_score > audio_formats[key].get('score', 0):
                    codec_label = " (AAC)" if is_aac else ""
                    audio_formats[key] = {
                        'format_id': format_id,
                        'label': label + codec_label,
                        'bitrate': f"{bitrate}kbps",
                        'ext': 'm4a',
                        'abr': bitrate,
                        'score': quality_score,
                        'codec': acodec
                    }

    # Chuyển sang list và sắp xếp
    video_list = sorted(video_formats.values(), key=lambda x: x['height'], reverse=True)
    audio_list = sorted(audio_formats.values(), key=lambda x: x['score'], reverse=True)

    # Giới hạn audio (lấy 2 mức tốt nhất)
    audio_list = audio_list[:2]

    return video_list, audio_list


def print_menu(video_formats, audio_formats, title):
    """In menu lựa chọn"""
    print("\n" + "="*70)
    print(f"📺 YOUTUBE DOWNLOADER - FASTER WHISPER EDITION")
    print("="*70)

    # Rút gọn title
    display_title = title if len(title) <= 60 else title[:57] + "..."
    print(f"\n📹 {display_title}")
    print("="*70)

    if video_formats:
        print("\n🎬 VIDEO (không tiếng - video only)")
        for i, fmt in enumerate(video_formats, 1):
            print(f"  {i}. {fmt['label']:<20} {fmt['resolution']:<10} .{fmt['ext']}")

    if audio_formats:
        print("\n🎵 AUDIO (cho faster_whisper)")
        audio_start = len(video_formats) + 1
        for i, fmt in enumerate(audio_formats, audio_start):
            print(f"  {i}. {fmt['label']:<30} {fmt['bitrate']:<10} .{fmt['ext']}")

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


def get_best_audio_format(info):
    """Tự động lấy format audio tốt nhất (AAC ưu tiên)"""
    formats = info.get('formats', [])
    best_audio = None
    best_score = 0
    
    for f in formats:
        vcodec = f.get('vcodec', 'none')
        acodec = f.get('acodec', 'none')
        abr = f.get('abr', 0)
        ext = f.get('ext', '')
        
        # Chỉ lấy audio-only
        if acodec != 'none' and vcodec == 'none' and ext in ['m4a', 'webm', 'mp4']:
            if abr:
                bitrate = int(abr)
                is_aac = 'aac' in acodec.lower() if acodec else False
                
                # Điểm ưu tiên: AAC + bitrate cao
                score = bitrate + (100 if is_aac else 0)
                
                if score > best_score:
                    best_score = score
                    best_audio = f.get('format_id')
    
    return best_audio


def download_video_and_audio(url, video_format_id, output_path="downloads"):
    """
    Tải video không tiếng + audio tốt nhất
    Kết quả: video_cn.mp4 + video_cn.m4a
    """
    Path(output_path).mkdir(exist_ok=True)

    # Lấy thông tin để tìm audio tốt nhất
    print("\n🔍 Đang tìm audio tốt nhất...")
    info = get_video_info(url)
    if not info:
        return
    
    best_audio_id = get_best_audio_format(info)
    if not best_audio_id:
        print("⚠️  Không tìm thấy audio, chỉ tải video")
        download_video_only(url, video_format_id, output_path)
        return
    
    # Tạo tên file an toàn
    base_name = 'video_cn'
    safe_video_filename = get_safe_filename(output_path, base_name, 'mp4')
    safe_audio_filename = get_safe_filename(output_path, base_name, 'm4a')
    
    # Tách số thứ tự (nếu có) để đồng bộ tên file
    if '_' in safe_video_filename:
        base_num = safe_video_filename.split('.')[0]  # video_cn_1
        safe_audio_filename = f"{base_num}.m4a"
    
    print(f"💾 Video file: {safe_video_filename}")
    print(f"💾 Audio file: {safe_audio_filename}")
    
    # Tải VIDEO (không tiếng)
    print(f"\n📥 [1/2] Đang tải video (không tiếng)...")
    video_template = f'{output_path}/{safe_video_filename}'
    
    ydl_opts_video = {
        'format': video_format_id,
        'outtmpl': video_template,
        'progress_hooks': [progress_hook],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
            ydl.download([url])
        print(f"\n✅ Video đã tải xong!")
    except Exception as e:
        print(f"\n❌ Lỗi khi tải video: {e}")
        return
    
    # Tải AUDIO (giữ nguyên AAC/M4A, không convert)
    print(f"\n📥 [2/2] Đang tải audio chất lượng cao (AAC)...")
    audio_template = f'{output_path}/{safe_audio_filename}'
    
    ydl_opts_audio = {
        'format': best_audio_id,
        'outtmpl': audio_template,
        'progress_hooks': [progress_hook],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts_audio) as ydl:
            ydl.download([url])
        print(f"\n✅ Audio đã tải xong!")
        
        print("\n" + "="*70)
        print("🎉 HOÀN THÀNH!")
        print("="*70)
        print(f"📁 Vị trí: {os.path.abspath(output_path)}")
        print(f"  📹 Video: {safe_video_filename}")
        print(f"  🎵 Audio: {safe_audio_filename}")
        print("\n💡 Bước tiếp theo:")
        print("  1. Dùng faster_whisper với file M4A để tạo SRT")
        print("  2. Dịch SRT sang tiếng Việt")
        print("  3. TTS tiếng Việt")
        print("  4. Ghép: video.mp4 + voice_vi.wav + audio_gốc (nhạc nền)")
        print("="*70)
        
    except Exception as e:
        print(f"\n❌ Lỗi khi tải audio: {e}")


def download_video_only(url, format_id, output_path="downloads"):
    """Tải chỉ video (fallback)"""
    Path(output_path).mkdir(exist_ok=True)
    safe_filename = get_safe_filename(output_path, 'video_cn', 'mp4')
    output_template = f'{output_path}/{safe_filename}'
    
    print(f"\n💾 Tên file: {safe_filename}")
    
    ydl_opts = {
        'format': format_id,
        'outtmpl': output_template,
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


def download_audio_only(url, format_id, output_path="downloads"):
    """Tải chỉ audio (giữ nguyên AAC/M4A)"""
    Path(output_path).mkdir(exist_ok=True)
    safe_filename = get_safe_filename(output_path, 'video_cn', 'm4a')
    output_template = f'{output_path}/{safe_filename}'
    
    print(f"\n💾 Tên file: {safe_filename}")
    
    ydl_opts = {
        'format': format_id,
        'outtmpl': output_template,
        'progress_hooks': [progress_hook],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            print("\n⬇️  Đang tải xuống audio AAC...")
            ydl.download([url])
            print("\n✅ Tải xuống hoàn tất!")
            print(f"📁 Lưu tại: {os.path.abspath(output_path)}/{safe_filename}")
    except Exception as e:
        print(f"\n❌ Lỗi khi tải: {e}")


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
                # Tải video + audio tốt nhất
                selected = video_formats[choice - 1]
                print(f"\n📌 Đã chọn: {selected['label']} {selected['resolution']}")
                print("💡 Sẽ tải video (không tiếng) + audio tốt nhất (AAC/M4A)")
                
                download_video_and_audio(url, selected['format_id'])

            elif len(video_formats) < choice <= total_options:
                # Tải chỉ audio
                audio_index = choice - len(video_formats) - 1
                selected = audio_formats[audio_index]
                print(f"\n📌 Đã chọn: Audio {selected['bitrate']}")
                download_audio_only(url, selected['format_id'])

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
    print("🎬 YOUTUBE DOWNLOADER - FASTER WHISPER EDITION")
    print("="*70)
    print("✨ Tính năng đặc biệt:")
    print("  • Tải video KHÔNG TIẾNG (video-only MP4)")
    print("  • Tải audio CHẤT LƯỢNG CAO (AAC/M4A, ưu tiên AAC)")
    print("  • Tự động tăng số tên file (video_cn_1, video_cn_2...)")
    print("  • Hoàn hảo cho faster_whisper -> dịch -> TTS -> lồng tiếng")
    print("  • Tiết kiệm dung lượng: M4A chỉ ~10% so với WAV")
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