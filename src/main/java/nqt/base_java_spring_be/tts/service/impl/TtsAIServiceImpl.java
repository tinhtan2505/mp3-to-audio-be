package nqt.base_java_spring_be.tts.service.impl;

import nqt.base_java_spring_be.repository.WordsRepository;
import nqt.base_java_spring_be.tts.dto.TextToMp3Request;
import nqt.base_java_spring_be.tts.dto.TextToMp3Result;
import nqt.base_java_spring_be.tts.service.iservices.TtsAIService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import org.springframework.http.*;

import javax.annotation.PostConstruct;
import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.*;

@Service
public class TtsAIServiceImpl implements TtsAIService {
    private final String configuredFfmpegPath;
    private String ffmpegCmdResolved;
    @Value("${app.tts.python-service-url:http://localhost:8000/api/v1/tts}")
    private String pythonServiceUrl;
    private final RestTemplate restTemplate;

    private final WordsRepository wordsRepository;

    public TtsAIServiceImpl(
            @Value("${app.tts.ffmpeg-path:}") String ffmpegPath,
            WordsRepository wordsRepository) {
        this.wordsRepository = wordsRepository;
        this.configuredFfmpegPath = ffmpegPath == null ? "" : ffmpegPath.trim();
        this.restTemplate = new RestTemplate();
    }

    @PostConstruct
    private void init() {
        try {
            this.ffmpegCmdResolved = locateFfmpeg();
            System.out.println("ffmpeg đã được xác định tại: " + this.ffmpegCmdResolved);
            // debug: show PATH visible to the JVM
            System.out.println("JVM PATH=" + System.getenv("PATH"));
        } catch (RuntimeException ex) {
            System.err.println("Không tìm thấy ffmpeg: " + ex.getMessage());
            this.ffmpegCmdResolved = null;
        }
    }

    // --- CÁC HÀM HỖ TRỢ FFmpeg & Hệ thống ---

    private String locateFfmpeg() {
        // 1) nếu cấu hình đường dẫn được cung cấp
        if (configuredFfmpegPath != null && !configuredFfmpegPath.isBlank()) {
            Path p = Paths.get(configuredFfmpegPath);
            if (Files.isExecutable(p)) return p.toAbsolutePath().toString();
            // nếu người dùng truyền folder thì thêm ffmpeg.exe
            if (Files.isDirectory(p)) {
                Path candidate = p.resolve(isWindows() ? "ffmpeg.exe" : "ffmpeg");
                if (Files.isExecutable(candidate)) return candidate.toAbsolutePath().toString();
            }
            // thử thêm .exe trên Windows nếu cần
            if (isWindows() && !configuredFfmpegPath.toLowerCase().endsWith(".exe")) {
                Path candidate = Paths.get(configuredFfmpegPath + ".exe");
                if (Files.isExecutable(candidate)) return candidate.toAbsolutePath().toString();
            }
            throw new RuntimeException("Đường dẫn ffmpeg đã cấu hình không tồn tại hoặc không thể thực thi: " + configuredFfmpegPath);
        }

        // 2) tìm trong PATH
        String pathEnv = System.getenv("PATH");
        if (pathEnv != null) {
            for (String part : pathEnv.split(File.pathSeparator)) {
                Path candidate = Paths.get(part, isWindows() ? "ffmpeg.exe" : "ffmpeg");
                if (Files.isExecutable(candidate)) {
                    return candidate.toAbsolutePath().toString();
                }
            }
        }

        // 3) fallback: thử command 'where' hoặc 'which'
        try {
            ProcessBuilder pb = isWindows() ?
                    new ProcessBuilder("cmd", "/c", "where ffmpeg") :
                    new ProcessBuilder("sh", "-c", "which ffmpeg");
            Process p = pb.start();
            try (BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                String line = br.readLine();
                if (line != null && !line.isBlank()) return line.trim();
            }
            p.waitFor();
        } catch (Exception ignored) {}

        throw new RuntimeException("Không tìm thấy ffmpeg trong PATH và không có cấu hình app.tts.ffmpeg-path");
    }

    private boolean isWindows() {
        return System.getProperty("os.name").toLowerCase().contains("win");
    }

    @Override
    public TextToMp3Result textToSpeech(TextToMp3Request req) {
        String sentence = req.getWord().toLowerCase();

        // 1. Tạo đường dẫn file tạm
        String tempDir = System.getProperty("java.io.tmpdir");
        String fileId = UUID.randomUUID().toString();
        Path wavPath = Paths.get(tempDir, fileId + ".wav");
        Path mp3Path = Paths.get(tempDir, fileId + ".mp3");

        try {
            // 2. GỌI PYTHON: Lấy dữ liệu file WAV
            byte[] wavBytes = callPythonTtsService(sentence);

            // Lưu file WAV xuống ổ cứng tạm thời
            Files.write(wavPath, wavBytes);

            // 3. GỌI FFMPEG: Convert WAV -> MP3
            if (ffmpegCmdResolved != null) {
                convertWavToMp3(wavPath.toString(), mp3Path.toString());

                // Đọc file MP3 lên bộ nhớ
                byte[] mp3Bytes = Files.readAllBytes(mp3Path);

                // Dọn dẹp file rác
                cleanupTempFiles(wavPath, mp3Path);

                // Trả về MP3
                return new TextToMp3Result(mp3Bytes, new ArrayList<>());
            } else {
                // Fallback: Nếu không có FFmpeg thì trả về file WAV gốc luôn
                System.out.println("WARN: Không tìm thấy FFmpeg, trả về định dạng WAV.");
                byte[] wavData = Files.readAllBytes(wavPath);
                cleanupTempFiles(wavPath, null);
                return new TextToMp3Result(wavData, new ArrayList<>());
            }

        } catch (Exception e) {
            e.printStackTrace();
            // Dọn dẹp nếu có lỗi
            cleanupTempFiles(wavPath, mp3Path);
            throw new RuntimeException("Lỗi xử lý TTS: " + e.getMessage());
        }
    }

    // --- CÁC HÀM CON (PRIVATE) ---

    private byte[] callPythonTtsService(String text) {
        try {
            HttpHeaders headers = new HttpHeaders();
            headers.setContentType(MediaType.APPLICATION_JSON);

            // Tạo JSON body: {"text": "xin chào"}
            Map<String, Object> body = new HashMap<>();
            body.put("text", text);

            HttpEntity<Map<String, Object>> entity = new HttpEntity<>(body, headers);

            // Gửi POST request sang localhost:8000
            ResponseEntity<byte[]> response = restTemplate.postForEntity(pythonServiceUrl, entity, byte[].class);

            if (response.getStatusCode().is2xxSuccessful() && response.getBody() != null) {
                return response.getBody();
            } else {
                throw new RuntimeException("Python Service lỗi: " + response.getStatusCode());
            }
        } catch (Exception e) {
            throw new RuntimeException("Không kết nối được tới Python VITS (Kiểm tra xem run_server.bat đã chạy chưa?): " + e.getMessage());
        }
    }

    private void convertWavToMp3(String inputPath, String outputPath) {
        try {
            // Lệnh: ffmpeg -y -i input.wav -acodec libmp3lame -q:a 2 output.mp3
            ProcessBuilder pb = new ProcessBuilder(
                    ffmpegCmdResolved,
                    "-y",                   // Ghi đè file nếu tồn tại
                    "-i", inputPath,        // Input
                    "-acodec", "libmp3lame",// Codec MP3 chuẩn
                    "-q:a", "2",            // Chất lượng cao (VBR)
                    outputPath              // Output
            );

            pb.redirectErrorStream(true); // Gộp log lỗi vào log thường
            Process p = pb.start();

            // Đọc log để tránh treo tiến trình (Deadlock)
            try (BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                String line;
                while ((line = br.readLine()) != null) {
                    // Uncomment dòng dưới nếu muốn xem log ffmpeg trong console
                    // System.out.println("[FFmpeg] " + line);
                }
            }

            int exitCode = p.waitFor();
            if (exitCode != 0) {
                throw new RuntimeException("FFmpeg thất bại với mã lỗi: " + exitCode);
            }
        } catch (Exception e) {
            throw new RuntimeException("Lỗi convert MP3: " + e.getMessage());
        }
    }

    private void cleanupTempFiles(Path wav, Path mp3) {
        try {
            if (wav != null) Files.deleteIfExists(wav);
            if (mp3 != null) Files.deleteIfExists(mp3);
        } catch (Exception ignored) {}
    }
}