package nqt.base_java_spring_be.tts.service.impl;

import nqt.base_java_spring_be.tts.dto.DubbingFileRequest;
import nqt.base_java_spring_be.tts.dto.DubbingResult;
import nqt.base_java_spring_be.tts.service.iservices.DubbingService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;

import java.io.File;
import java.util.HashMap;
import java.util.Map;

@Service
public class DubbingServiceImpl implements DubbingService {
    private final RestTemplate restTemplate;
    @Value("${app.tts.python-dubbing-url:http://localhost:8001/api/v1/dubbing}")
    private String pythonDubbingUrl;

    public DubbingServiceImpl() {
        this.restTemplate = new RestTemplate();
    }

    @Override
    public DubbingResult dubbingFromWhisper(DubbingFileRequest req) {
        String inputPath = req.getInputPath();

        // 1. Validate đường dẫn
        File f = new File(inputPath);
        if (!f.exists()) {
            throw new RuntimeException("File input không tồn tại trên server: " + inputPath);
        }

        try {
            // 2. Gọi Python Service (Nhận về JSON Map thay vì byte[])
            Map<String, Object> pythonResponse = callPythonDubbingService(inputPath);

            // 3. Kiểm tra trạng thái từ Python trả về
            String status = (String) pythonResponse.get("status");

            if ("success".equalsIgnoreCase(status)) {
                String outputFilePath = (String) pythonResponse.get("output_file");
                System.out.println("Python xử lý thành công. File output: " + outputFilePath);

                // Vì hiện tại chỉ tạo file SRT (Text), chưa có file MP3,
                // ta trả về một mảng byte rỗng và danh sách rỗng để Controller không bị lỗi.
                // Bạn có thể sửa DTO DubbingResult để chứa thêm message hoặc đường dẫn file.
                return new DubbingResult("success", outputFilePath);
            } else {
                throw new RuntimeException("Python trả về lỗi logic: " + pythonResponse);
            }

        } catch (Exception e) {
            e.printStackTrace();
            throw new RuntimeException("Lỗi Dubbing Service: " + e.getMessage());
        }
    }

    // --- HELPER GỌI PYTHON ---
    private Map<String, Object> callPythonDubbingService(String inputPath) {
        try {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);

            // Tạo body JSON: {"input_path": "D:/..."}
            Map<String, Object> body = new HashMap<>();
            body.put("input_path", inputPath);

            HttpEntity<Map<String, Object>> entity = new HttpEntity<>(body, headers);

            // QUAN TRỌNG: Thay đổi kiểu trả về từ byte[].class sang Map.class
            // Sử dụng ParameterizedTypeReference để hứng JSON
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    pythonDubbingUrl,
                    org.springframework.http.HttpMethod.POST,
                    entity,
                    new ParameterizedTypeReference<Map<String, Object>>() {}
            );

            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                return response.getBody();
            } else {
                throw new RuntimeException("Python Dubbing API lỗi HTTP: " + response.getStatusCode());
            }
        } catch (Exception e) {
            throw new RuntimeException("Không kết nối được tới Python Service: " + e.getMessage());
        }
    }
}