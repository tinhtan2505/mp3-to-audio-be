import pysrt
from deep_translator import GoogleTranslator
import time
import os
from datetime import datetime

INPUT = "output.srt"

# --- CẬP NHẬT 1: TẠO TÊN FILE OUTPUT ĐỘNG ---
# Lấy tên file gốc không bao gồm đuôi .srt (ví dụ: "output")
base_name = os.path.splitext(INPUT)[0]
# Lấy thời gian hiện tại: NgàyThángNăm_GiờPhútGiây
current_time = datetime.now().strftime("%d%m%Y_%H%M%S")
# Tạo tên file mới
OUTPUT = f"{base_name}_vi_{current_time}.srt"

subs = pysrt.open(INPUT)
translator = GoogleTranslator(source='zh-CN', target='vi')

print(f"Đang dịch file: {INPUT}")
print(f"File đích sẽ là: {OUTPUT}")
print("------------------------------------------------")

for i, sub in enumerate(subs):
    try:
        if sub.text.strip():
            # Lưu lại text gốc trước khi dịch để in ra
            original_text = sub.text

            # Thực hiện dịch
            translated_text = translator.translate(original_text)

            # Gán text mới vào phụ đề
            sub.text = translated_text

            # --- CẬP NHẬT 2: IN RA TEXT GỐC -> TEXT DỊCH ---
            # In ra log (bỏ check % 10 để bạn nhìn thấy toàn bộ,
            # hoặc nếu muốn ít log hơn thì thêm lại if i % 10 == 0)
            print(f"Dòng {i}: '{original_text}' -> '{translated_text}'")

    except Exception as e:
        print(f"Lỗi tại dòng {i}: {e}")
        pass

# Lưu file
subs.save(OUTPUT, encoding='utf-8')
print("------------------------------------------------")
print(f"Xong! File đã được lưu tại: {OUTPUT}")