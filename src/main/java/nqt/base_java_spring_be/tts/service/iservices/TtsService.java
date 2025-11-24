package nqt.base_java_spring_be.tts.service.iservices;

import nqt.base_java_spring_be.entity.Words;
import nqt.base_java_spring_be.tts.dto.TextToMp3Request;
import nqt.base_java_spring_be.tts.dto.TextToMp3Result;
import nqt.base_java_spring_be.tts.dto.ViettelTtsRequest;
import org.springframework.http.*;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.stream.Stream;

public interface TtsService {
//    void insertWords();
//    byte[] speechSynthesis();
    TextToMp3Result textToMp3(TextToMp3Request req);
}

//public void insertWords(){
//    String directoryPath = "D:/My Project/MP3_TO_AUDIO/mp3-viet-11k";
//    File folder = new File(directoryPath);
//
//    if (!folder.exists() || !folder.isDirectory()) {
//        throw new IllegalArgumentException("Thư mục không tồn tại: " + directoryPath);
//    }
//
//    File[] files = folder.listFiles((dir, name) -> name.toLowerCase().endsWith(".mp3"));
//    List<Words> words = new ArrayList<>();
//    if (files != null) {
//        for (File file : files) {
//            String fileName = file.getName(); // abc.mp3
//            int dotIndex = fileName.lastIndexOf('.');
//
//            String nameWithoutExt = (dotIndex > 0) ? fileName.substring(0, dotIndex) : fileName;
//            String extension = (dotIndex > 0) ? fileName.substring(dotIndex + 1) : "";
//
//            Optional<Words> opt = wordsRepository.findActiveByName(nameWithoutExt);
//            if (opt.isEmpty()) {
//                Words word = new Words(file.getName(), nameWithoutExt, extension, file.getAbsolutePath());
//                words.add(word);
//            }
//        }
//    }
//    wordsRepository.saveAll(words);
//}
//
//public byte[] speechSynthesis() {
//    String filePath = "src/main/java/nqt/base_java_spring_be/data/Viet11K.txt";
//    Path path = Paths.get(filePath);
//
//    if (!Files.exists(path)) {
//        System.err.println("Không tìm thấy file tại: " + path.toAbsolutePath());
//        return null;
//    }
//
//    System.out.println("--- BẮT ĐẦU QUÉT TÌM TỪ MỚI ---");
//
//    List<String> wordsToProcess = new ArrayList<>();
//
//    // BƯỚC 1: Lọc ra 10 từ chưa có trong DB và đưa vào List
//    // Dùng try-with-resources để đóng file ngay sau khi đọc xong
//    try (Stream<String> lines = Files.lines(path)) {
//        wordsToProcess = lines
//                .map(String::trim)
//                .filter(text -> !text.isEmpty())
//                .filter(text -> {
//                    // Kiểm tra DB, nếu có rồi thì bỏ qua
//                    return wordsRepository.findFirstByNameIgnoreCase(text).isEmpty();
//                })
//                .limit(10) // Chỉ lấy 10 từ
//                .toList(); // Java 16+ (hoặc .collect(Collectors.toList()) với Java thấp hơn)
//    } catch (IOException e) {
//        e.printStackTrace();
//        return null;
//    }
//
//    if (wordsToProcess.isEmpty()) {
//        System.out.println("Không tìm thấy từ mới nào (hoặc đã tải hết).");
//        return null;
//    }
//
//    // BƯỚC 2: Chạy vòng lặp For truyền thống để dễ dàng BREAK
//    System.out.println("Tìm thấy " + wordsToProcess.size() + " từ cần xử lý. Bắt đầu tải...");
//    int successCount = 0;
//
//    for (String textToSpeak : wordsToProcess) {
//        System.out.println(">>> Đang xử lý: " + textToSpeak);
//        try {
//            // Gọi hàm tải. Hàm này cần ném ra Exception nếu lỗi (xem cập nhật bên dưới)
//            speechSynthesisViettel(textToSpeak);
//            successCount++;
//
//            // Nếu thành công thì ngủ 1 chút
//            Thread.sleep(1000);
//
//        } catch (Exception e) {
//            // NẾU GẶP BẤT KỲ LỖI GÌ (429, Mất mạng,...) -> DỪNG NGAY
//            System.err.println("!!! GẶP LỖI NGHIÊM TRỌNG KHI TẢI TỪ: " + textToSpeak);
//            System.err.println("!!! Chi tiết lỗi: " + e.getMessage());
//            System.err.println("!!! -> DỪNG VÒNG LẶP NGAY LẬP TỨC.");
//
//            break; // <--- LỆNH QUAN TRỌNG NHẤT: Thoát khỏi vòng for
//        }
//    }
//    System.out.println("\n========================================");
//    System.out.println("   THÔNG BÁO KẾT THÚC");
//    System.out.println("========================================");
//    System.out.println("Tổng số từ dự kiến: " + wordsToProcess.size());
//    System.out.println("Số từ thành công  : " + successCount);
//
//    if (successCount < wordsToProcess.size()) {
//        System.out.println("TRẠNG THÁI: DỪNG SỚM DO CÓ LỖI.");
//    } else {
//        System.out.println("TRẠNG THÁI: HOÀN THÀNH 100%.");
//    }
//    System.out.println("========================================\n");
//    return null;
//}
//
//public void speechSynthesisViettel(String text) {
//    // Cấu hình Header
//    String token = "d5623057def0e03b28ca77ca9f0b962d";
//    HttpHeaders headers = new HttpHeaders();
//    headers.setContentType(MediaType.APPLICATION_JSON);
//    headers.set("accept", "*/*");
//
//    ViettelTtsRequest requestBody = ViettelTtsRequest.builder()
//            .text(text)
//            .voice("hn-thanhphuong") // Giọng nữ miền Bắc
//            .speed(1.0f)
//            .ttsReturnOption(3) // 3 = mp3
//            .token(token)
//            .withoutFilter(false)
//            .build();
//
//    HttpEntity<ViettelTtsRequest> entity = new HttpEntity<>(requestBody, headers);
//
//    // Gọi API
//    try {
//        ResponseEntity<byte[]> response = restTemplate.exchange(
//                VIETTEL_API_URL,
//                HttpMethod.POST,
//                entity,
//                byte[].class
//        );
//
//        if (response.getStatusCode() == HttpStatus.OK && response.getBody() != null) {
//            byte[] audioBytes = response.getBody();
//
//            // Lưu file
//            String outputDir = "D:/BackUp Db/mp3-output";
//            Path dirPath = Paths.get(outputDir);
//            if (!Files.exists(dirPath)) Files.createDirectories(dirPath);
//
//            String fileName = text.trim() + ".mp3";
//            Path filePath = dirPath.resolve(fileName);
//            Files.write(filePath, audioBytes);
//            System.out.println("   -> Đã lưu file: " + fileName);
//
//            // Lưu DB
//            Words newWord = Words.builder()
//                    .name(text)
//                    .fullName(fileName)
//                    .path(filePath.toAbsolutePath().toString())
//                    .type("mp3")
//                    .build();
//            newWord.setActive(true);
//            wordsRepository.save(newWord);
//            System.out.println("   -> Đã lưu DB.");
//
//        } else {
//            // Ném lỗi nếu status code không phải 200
//            throw new RuntimeException("API trả về lỗi status: " + response.getStatusCode());
//        }
//    } catch (Exception e) {
//        // Ném lỗi ra ngoài để vòng lặp for bắt được và dừng lại
//        // In lỗi để debug
//        // System.err.println("Lỗi API: " + e.getMessage());
////            throw e; // <--- QUAN TRỌNG: Phải ném lỗi ra ngoài
//        throw new RuntimeException("API trả về lỗi status: " + e.getMessage());
//    }
//}