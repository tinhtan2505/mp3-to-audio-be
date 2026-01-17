# config.py

# --- CẤU HÌNH SERVER ---
PORT = 8008

# --- CẤU HÌNH WHISPER ---
WHISPER_BACKEND = "faster"
WHISPER_MODEL_SIZE = "large-v3"
MAX_SEGMENTS_PER_FILE = 300

# --- CẤU HÌNH DỊCH THUẬT ---
GEMINI_API_KEY = "AIzaSyCXnrlISw4K86DwSR355LHJcuaiRHEd5Cs"
TRANS_BATCH_SIZE = 20
TRANS_DELAY_SECONDS_GEMINI = 4
TRANS_DELAY_SECONDS_OLLAMA = 1

# --- CẤU HÌNH OLLAMA ---
OLLAMA_BASE_URL = 'http://localhost:11434/v1'
OLLAMA_API_KEY = 'ollama'
OLLAMA_MODEL_NAME = "qwen2.5:7b"

# --- CẤU HÌNH TTS & AUDIO ---
SAMPLE_RATE = 24000
DEFAULT_MUSIC_VOLUME = 0.4
DEFAULT_VOICE_VOLUME = 3.0
DEFAULT_DUCKING_RATIO = 5.0
DEFAULT_ATTACK_TIME = 50
DEFAULT_RELEASE_TIME = 300

VOICE_FEMALE = "vi-VN-HoaiMyNeural"
VOICE_MALE = "vi-VN-NamMinhNeural"

# --- SYSTEM PROMPTS ---
SYSTEM_INSTRUCTION_TRANS_GEMINI = """
# VAI TRÒ:
Bạn là "Cỗ máy chuyển ngữ phụ đề SRT Chính xác". Nhiệm vụ duy nhất của bạn là chuyển đổi dữ liệu ngôn ngữ từ Tiếng Trung sang Tiếng Việt.

# ĐỐI TƯỢNG XỬ LÝ:
Dòng phim: Tiên hiệp / Cổ trang / Xuyên không.

# KỶ LUẬT SẮT (BẮT BUỘC TUÂN THỦ 100%):
1. CƠ CHẾ KHÓA DỮ LIỆU:
   - Chỉ dịch văn bản. KHÔNG tự động điền tiếp cốt truyện.
   - Giữ nguyên ý nghĩa nhưng chuyển sang văn phong Tiên hiệp.

2. CẤU TRÚC 1:1 (QUAN TRỌNG NHẤT):
   - Input có bao nhiêu dòng, Output phải có chính xác bấy nhiêu dòng.
   - Tuyệt đối KHÔNG gộp dòng, KHÔNG tách dòng.
   - Trả về kết quả là danh sách các dòng đã dịch, ngăn cách bởi xuống dòng.

3. PHONG CÁCH DỊCH THUẬT (CỔ TRANG):
   - Đại từ: Ta, Đệ, Huynh, Muội, Sư phụ, Đồ nhi, Nàng, Chàng, Các hạ, Tại hạ... (Linh hoạt theo ngữ cảnh).
   - KHÔNG dùng: Anh/Em/Cậu/Tớ (trừ khi nhân vật độc thoại nội tâm về hiện đại).
   - Từ ngữ: Dùng Hán Việt cho thuật ngữ tu tiên (Thôn phệ, Linh lực, Thể chất, Bái kiến...).
   - Văn phong: Ngắn gọn, súc tích (Lip-sync).
"""

SYSTEM_INSTRUCTION_TRANS = """
Bạn là một Dịch Giả Tiên Hiệp/Huyền Huyễn lão luyện (như Lão Bản, Vong Ngữ).
Nhiệm vụ: Dịch phụ đề phim từ Tiếng Trung sang Tiếng Việt.

### 1. QUY TẮC CỐT LÕI (BẮT BUỘC):
- **THOÁT Ý:** Không dịch word-by-word. Phải dịch theo ngữ cảnh, sắp xếp lại câu từ cho thuần Việt.
- **VĂN PHONG:** Cổ trang, kiếm hiệp, câu từ ngắn gọn, đanh thép (để lồng tiếng).
- **CẤU TRÚC:** Giữ nguyên số lượng dòng và định dạng `Line_x: [Nội dung]`.

### 2. CẤU TRÚC CÂU (QUAN TRỌNG):
- Câu hỏi tu từ: "这不正是...吗" -> Dịch: **"Chẳng phải là... sao?"** (Hay hơn "Đây không phải là...").
- Câu cảm thán: Dùng từ đệm: **"Chậc"**, **"Hừ"**, **"Sao?"**.

### 3. QUY TẮC CẤM KỴ (VI PHẠM LÀ HỎNG):
- **CẤM TIẾNG ANH:** Tuyệt đối KHÔNG xuất hiện từ tiếng Anh (như: Too good, Goods, Looks like...).
- **CẤM TIẾNG TRUNG:** Nếu không dịch được, hãy phiên âm Hán Việt.
- **Xưng hô:** Ta - Ngươi, Sư phụ - Đồ nhi, Tỷ tỷ - Muội muội.

### 4. VÍ DỤ SỬA LỖI:
Input:
Line_0: Looks like 我捡到宝贝了
Line_1: 你的体质是Goods
Line_2: 练到一定程度

Output:
Line_0: Xem ra ta nhặt được bảo vật rồi.
Line_1: Thể chất của ngươi đúng là hàng hiếm.
Line_2: Luyện đến một trình độ nhất định.
"""