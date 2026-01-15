import os
import math

# ==========================================
# CẤU HÌNH
# ==========================================
INPUT_PATH = r"D:\\Dubbing\\project_1\\test\\1_cn.srt"
MAX_SEGMENTS_PER_FILE = 300

def split_srt_file():
    # 1. Kiểm tra file tồn tại
    if not os.path.exists(INPUT_PATH):
        print(f"❌ Lỗi: Không tìm thấy file tại {INPUT_PATH}")
        return

    print(f"📂 Đang đọc file: {INPUT_PATH}")

    try:
        # 2. Đọc nội dung file
        with open(INPUT_PATH, 'r', encoding='utf-8') as f:
            content = f.read()

        # Chuẩn hóa xuống dòng (phòng trường hợp Windows/Linux khác nhau)
        content = content.replace('\r\n', '\n')

        # Tách các block dựa trên 2 dấu xuống dòng liên tiếp (\n\n)
        # Mỗi block sẽ bao gồm: Index, Timestamp, Text
        blocks = content.split('\n\n')

        # Lọc bỏ các block rỗng (thường do file kết thúc bằng nhiều dấu xuống dòng)
        blocks = [b.strip() for b in blocks if b.strip()]

        total_segments = len(blocks)
        if total_segments == 0:
            print("⚠️ File rỗng hoặc format không đúng.")
            return

        print(f"📊 Tổng số câu: {total_segments}")
        print(f"✂️  Cấu hình cắt: {MAX_SEGMENTS_PER_FILE} câu/file")

        # 3. Tính toán và chia file
        # Tạo danh sách các chunk (mỗi chunk là một list các block)
        chunks = [blocks[i:i + MAX_SEGMENTS_PER_FILE]
                  for i in range(0, total_segments, MAX_SEGMENTS_PER_FILE)]

        dir_name = os.path.dirname(INPUT_PATH)
        base_name = os.path.splitext(os.path.basename(INPUT_PATH))[0]

        # Lấy tên gốc để đặt tên file con (bỏ chữ part cũ nếu có)
        # Ví dụ: 1_cn.srt -> 1_cn
        clean_base_name = base_name.split('_part')[0]

        print("\n🚀 BẮT ĐẦU GHI FILE...")

        for idx, chunk in enumerate(chunks):
            # Tạo tên file: 1_cn_part01.srt, 1_cn_part02.srt
            part_suffix = f"_part{idx+1:02d}"
            new_filename = f"{clean_base_name}{part_suffix}.srt"
            new_path = os.path.join(dir_name, new_filename)

            # Lấy index đầu và cuối để in log cho đẹp
            # Block structure: "Index\nTime\nText"
            first_index = chunk[0].split('\n')[0]
            last_index = chunk[-1].split('\n')[0]

            with open(new_path, 'w', encoding='utf-8') as f_out:
                # Nối các block lại bằng 2 dấu xuống dòng
                f_out.write('\n\n'.join(chunk))
                # Thêm dấu xuống dòng cuối file cho đúng chuẩn SRT
                f_out.write('\n')

            print(f"   ✅ Đã tạo: {new_filename}")
            print(f"      👉 {len(chunk)} câu (Index: {first_index} -> {last_index})")
            print(f"      📂 Path: {new_path}")

        print("\n✨ HOÀN THÀNH!")

    except Exception as e:
        print(f"❌ Có lỗi xảy ra: {e}")

if __name__ == "__main__":
    split_srt_file()