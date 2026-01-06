package nqt.base_java_spring_be.tts.service.impl;

import nqt.base_java_spring_be.tts.dto.DubbingFileRequest;
import nqt.base_java_spring_be.tts.dto.DubbingResult;
import nqt.base_java_spring_be.tts.dto.MixVideoRequest;
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
    @Value("${app.tts.python-whisper-url}")
    private String pythonWhisperUrl;

    @Value("${app.tts.python-tts-url}")
    private String pythonTtsUrl;

    @Value("${app.tts.python-mix-url}")
    private String pythonMixUrl;

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
                    pythonWhisperUrl,
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

    @Override
    public DubbingResult generateDubbingAudio(DubbingFileRequest req) {
        String inputPath = req.getInputPath();

        // 1. Kiểm tra file SRT tồn tại
        File f = new File(inputPath);
        if (!f.exists()) {
            throw new RuntimeException("File SRT không tồn tại: " + inputPath);
        }

        try {
            // 2. Gọi Python Server (8002)
            Map<String, Object> pythonResponse = callPythonTtsGenService(inputPath);

            // 3. Xử lý kết quả
            String status = (String) pythonResponse.get("status");
            if ("success".equalsIgnoreCase(status)) {
                String outputFilePath = (String) pythonResponse.get("output_file");
                System.out.println("-> Python 8002 trả về audio tại: " + outputFilePath);

                // Trả về đường dẫn file wav
                // (audioData rỗng vì file nằm trên ổ cứng, không cần load vào RAM)
                return new DubbingResult("", outputFilePath);
            } else {
                throw new RuntimeException("Python trả về lỗi logic: " + pythonResponse);
            }

        } catch (Exception e) {
            e.printStackTrace();
            throw new RuntimeException("Lỗi khi gọi Python TTS Gen: " + e.getMessage());
        }
    }

    // Helper gọi Python
    private Map<String, Object> callPythonTtsGenService(String inputPath) {
        try {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);

            // Body JSON: {"input_srt_path": "D:\\Dubbing\\pmh_vi.srt"}
            Map<String, Object> body = new HashMap<>();
            body.put("input_srt_path", inputPath);

            HttpEntity<Map<String, Object>> entity = new HttpEntity<>(body, headers);

            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    pythonTtsUrl,
                    org.springframework.http.HttpMethod.POST,
                    entity,
                    new ParameterizedTypeReference<Map<String, Object>>() {}
            );

            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                return response.getBody();
            } else {
                throw new RuntimeException("Lỗi HTTP từ Python: " + response.getStatusCode());
            }
        } catch (Exception e) {
            throw new RuntimeException("Không kết nối được tới Python Service (Port 8002): " + e.getMessage());
        }
    }

    @Override
    public DubbingResult mixVideo(MixVideoRequest req) {
        // 1. Validate sơ bộ
        if (req.getVideoInput() == null || req.getInstrumental() == null || req.getVoiceDub() == null) {
            throw new RuntimeException("Thiếu đường dẫn file input (Video, Music hoặc Voice)");
        }

        try {
            // 2. Chuẩn bị Body gửi sang Python
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);

            // Map key Java sang key Python (snake_case)
            Map<String, Object> body = new HashMap<>();
            body.put("video_input", req.getVideoInput());
            body.put("instrumental", req.getInstrumental());
            body.put("voice_dub", req.getVoiceDub());

            HttpEntity<Map<String, Object>> entity = new HttpEntity<>(body, headers);

            // 3. Gọi Python (Port 8003)
            ResponseEntity<Map<String, Object>> response = restTemplate.exchange(
                    pythonMixUrl,
                    org.springframework.http.HttpMethod.POST,
                    entity,
                    new ParameterizedTypeReference<Map<String, Object>>() {}
            );

            // 4. Xử lý kết quả
            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                Map<String, Object> respBody = response.getBody();
                String status = (String) respBody.get("status");

                if ("success".equalsIgnoreCase(status)) {
                    String outputFilePath = (String) respBody.get("output_file");
                    System.out.println("-> Video hoàn chỉnh tại: " + outputFilePath);

                    // Trả về đường dẫn video cuối cùng
                    return new DubbingResult("", outputFilePath);
                } else {
                    throw new RuntimeException("Python Mix lỗi: " + respBody);
                }
            } else {
                throw new RuntimeException("Lỗi kết nối Mix Server: " + response.getStatusCode());
            }

        } catch (Exception e) {
            e.printStackTrace();
            throw new RuntimeException("Lỗi Service Mix: " + e.getMessage());
        }
    }
}