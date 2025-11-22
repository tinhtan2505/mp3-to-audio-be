package nqt.base_java_spring_be.tts.service.impl;

import nqt.base_java_spring_be.entity.Words;
import nqt.base_java_spring_be.repository.WordsRepository;
import nqt.base_java_spring_be.tts.dto.TextToMp3Request;
import nqt.base_java_spring_be.tts.dto.TextToMp3Result;
import nqt.base_java_spring_be.tts.dto.ViettelTtsRequest;
import nqt.base_java_spring_be.tts.service.iservices.TtsService;
import nqt.base_java_spring_be.utils.StreamGobbler;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestTemplate;
import org.springframework.http.*;

import javax.annotation.PostConstruct;
import java.io.BufferedReader;
import java.io.File;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.file.*;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;

@Service
public class TtsServiceImpl implements TtsService {
    private final String configuredFfmpegPath;
    private String ffmpegCmdResolved;
    private static final int MAX_SEARCH_WORDS = 2;
    private static final double COMPOUND_WORD_SPLIT_PAUSE_SEC = 0.01;
    private final RestTemplate restTemplate = new RestTemplate();
    // URL từ tài liệu Viettel AI
    private static final String VIETTEL_API_URL = "https://viettelai.vn/tts/speech_synthesis";

    private final WordsRepository wordsRepository;

    public TtsServiceImpl(
            @Value("${app.tts.ffmpeg-path:}") String ffmpegPath,
            WordsRepository wordsRepository) {
        this.wordsRepository = wordsRepository;
        this.configuredFfmpegPath = ffmpegPath == null ? "" : ffmpegPath.trim();
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

    private Path createSilenceWavFile(Double durationSec) {
        if (durationSec == null || durationSec <= 0) return null;

        if (ffmpegCmdResolved == null) {
            throw new IllegalStateException("ffmpeg không khả dụng.");
        }

        try {
            // Tạo file silence WAV để không có padding MP3
            Path tmpFile = Files.createTempFile("sil_", ".wav");
            tmpFile.toFile().deleteOnExit();

            List<String> cmd = Arrays.asList(
                    ffmpegCmdResolved, "-y",
                    "-f", "lavfi",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-t", String.valueOf(durationSec),
                    tmpFile.toAbsolutePath().toString()
            );

            Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
            int rc = p.waitFor();

            if (rc != 0) {
                Files.deleteIfExists(tmpFile);
                throw new RuntimeException("ffmpeg thoát với mã lỗi " + rc + " khi tạo file silence WAV.");
            }

            return tmpFile;

        } catch (Exception e) {
            throw new RuntimeException("Lỗi khi tạo file silence WAV: " + durationSec + "s", e);
        }
    }

    /**
     * Xử lý cắt khoảng lặng VÀ điều chỉnh ngữ điệu (Pitch/Duration) trong cùng 1 lệnh FFmpeg.
     * * @param positionType: "start", "mid", "end"
     */
    private String processAudioSegment(String inputPath, Path tempDir, String positionType) {

        if (inputPath == null || inputPath.isBlank()) {
            throw new IllegalArgumentException("Input path is null");
        }
        if (ffmpegCmdResolved == null) throw new IllegalStateException("FFmpeg not found");

        String fileName = new File(inputPath).getName();
        String outputFileName = "seg_" + positionType + "_" + UUID.randomUUID().toString() + ".wav";
        Path outputPath = tempDir.resolve(outputFileName);

        // 1. Base Filter: Cắt khoảng lặng (Giữ nguyên thông số bạn đang dùng ok)
        // Lưu ý: stop_duration=0.05 để đuôi gọn gàng
        StringBuilder filterBuilder = new StringBuilder();
        filterBuilder.append("silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB:stop_periods=1:stop_duration=0.05:stop_threshold=-50dB");

        // 2. Prosody Filter: Điều chỉnh theo vị trí
        // Lưu ý quan trọng: Sau khi dùng asetrate (đổi sample rate), PHẢI dùng aresample=44100 để đưa về chuẩn chung, nếu không ghép file sẽ lỗi.
        switch (positionType) {
            case "start":
                // Đầu câu: Tăng tone 4% và nhanh hơn 4% -> Tạo năng lượng khởi đầu
                // asetrate=44100*1.04
                filterBuilder.append(",asetrate=45864,aresample=44100");
                break;

            case "end":
                // Cuối câu: Giảm tone 8% và chậm hơn 8% -> Tạo cảm giác xuống giọng kết thúc
                // asetrate=44100*0.92
                filterBuilder.append(",asetrate=40572,aresample=44100");
                break;

            case "mid":
            default:
                // Giữa câu: Giữ nguyên (hoặc có thể tăng rất nhẹ 1.01 nếu muốn giọng bay bổng)
                // Không làm gì thêm
                break;
        }

        // Chuẩn hóa định dạng đầu ra (PCM WAV 16bit, 44100Hz, Stereo) để dễ ghép
        List<String> cmd = Arrays.asList(
                ffmpegCmdResolved, "-y",
                "-i", inputPath,
                "-af", filterBuilder.toString(),
                "-c:a", "pcm_s16le",
                "-ar", "44100",
                "-ac", "2",
                outputPath.toAbsolutePath().toString()
        );

        try {
            ProcessBuilder pb = new ProcessBuilder(cmd);
            pb.redirectErrorStream(true);
            Process p = pb.start();

            // Gobbler để tránh treo process nếu buffer đầy
            new Thread(new StreamGobbler(p.getInputStream(), s -> {})).start();

            int rc = p.waitFor();
            if (rc != 0) {
                Files.deleteIfExists(outputPath);
                throw new RuntimeException("Lỗi xử lý audio segment (" + positionType + "), mã thoát=" + rc);
            }
            return outputPath.toAbsolutePath().toString();
        } catch (Exception e) {
            throw new RuntimeException("Lỗi khi xử lý file: " + inputPath, e);
        }
    }

    private String performFfmpegConcatFilterComplex(List<String> inputs, String outputFilePath, Path workingDir) {
        if (inputs == null || inputs.isEmpty()) {
            throw new IllegalArgumentException("Danh sách file đầu vào trống.");
        }
        if (ffmpegCmdResolved == null) {
            throw new IllegalStateException("FFmpeg chưa được cấu hình.");
        }

        List<String> cmd = new ArrayList<>();
        cmd.add(ffmpegCmdResolved);
        cmd.add("-y");

        // 1. Input tất cả các file
        for (String in : inputs) {
            cmd.add("-i");
            cmd.add(in);
        }

        StringBuilder filter = new StringBuilder();
        int lastIdx = inputs.size() - 1;
        String lastNodeLabel;

        // 2. Xử lý hạ giọng từ cuối cùng (Pre-processing Last Word)
        // Nếu chỉ có 1 từ hoặc nhiều từ, từ cuối cùng luôn cần hạ giọng.
        // asetrate=44100*0.92: Giảm tone và tốc độ xuống còn 92% (Trầm hơn, chậm hơn)
        // aresample=44100: Đưa sample rate về lại chuẩn để khớp với các file khác
        filter.append("[").append(lastIdx).append(":a]")
                .append("asetrate=44100*0.92,aresample=44100")
                .append("[last_mod];");

        lastNodeLabel = "[last_mod]";

        // 3. Logic nối file (Crossfade Loop)
        String currentStream = "[0:a]"; // Bắt đầu với file đầu tiên

        if (inputs.size() > 1) {
            for (int i = 0; i < inputs.size() - 1; i++) {
                // Input 1: Là file hiện tại (hoặc kết quả nối trước đó)
                String inputLabel1 = (i == 0) ? "[0:a]" : "[tmp" + i + "]";

                // Input 2:
                // Nếu đây là lần nối cuối cùng -> Lấy file đã hạ giọng [last_mod]
                // Nếu chưa phải cuối cùng -> Lấy file tiếp theo [i+1:a]
                String inputLabel2 = (i == inputs.size() - 2) ? "[last_mod]" : "[" + (i + 1) + ":a]";

                // Output:
                String outputLabel = "[tmp" + (i + 1) + "]";

                filter.append(inputLabel1)
                        .append(inputLabel2)
                        // d=0.04: Crossfade 40ms
                        .append("acrossfade=d=0.04:c1=tri:c2=tri")
                        .append(outputLabel)
                        .append(";");

                currentStream = outputLabel;
            }
        } else {
            // Nếu chỉ có 1 file duy nhất, thì chính file đó là [last_mod]
            currentStream = "[last_mod]";
        }

        // Xóa dấu ; thừa nếu có
        if (filter.length() > 0 && filter.charAt(filter.length() - 1) == ';') {
            filter.deleteCharAt(filter.length() - 1);
        }

        // 4. Giai đoạn Hậu kỳ (Post-Processing)
        // Nối thêm atempo và loudnorm
        if (filter.length() > 0) filter.append(";");

        filter.append(currentStream)
                .append("atempo=1.15,loudnorm=I=-16:TP=-1.5:LRA=11")
                .append("[out_final]");

        cmd.add("-filter_complex");
        cmd.add(filter.toString());

        cmd.add("-map");
        cmd.add("[out_final]");

        // 5. Encode Output
        cmd.add("-c:a"); cmd.add("libmp3lame");
        cmd.add("-b:a"); cmd.add("192k");
        cmd.add("-ar"); cmd.add("44100");
        cmd.add("-avoid_negative_ts"); cmd.add("make_zero");

        cmd.add(outputFilePath);

        // 6. Thực thi
        try {
            ProcessBuilder pb = new ProcessBuilder(cmd);
            if (workingDir != null) pb.directory(workingDir.toFile());
            pb.redirectErrorStream(true);
            Process p = pb.start();

            StringBuilder outputLog = new StringBuilder();
            Thread logger = new Thread(new StreamGobbler(p.getInputStream(), line -> {
                outputLog.append(line).append(System.lineSeparator());
            }));
            logger.start();

            int rc = p.waitFor();
            logger.join();

            if (rc != 0) {
                throw new RuntimeException("FFmpeg Error Log:\n" + outputLog.toString());
            }
            return outputFilePath;

        } catch (Exception e) {
            throw new RuntimeException("Lỗi ghép file", e);
        }
    }

    // --- HÀM XỬ LÝ DATABASE & TOKENIZATION ---

    public void insertWords(){
        String directoryPath = "D:/BackUp Db/mp3-output";
        File folder = new File(directoryPath);

        if (!folder.exists() || !folder.isDirectory()) {
            throw new IllegalArgumentException("Thư mục không tồn tại: " + directoryPath);
        }

        File[] files = folder.listFiles((dir, name) -> name.toLowerCase().endsWith(".mp3"));
        List<Words> words = new ArrayList<>();
        if (files != null) {
            for (File file : files) {
                String fileName = file.getName(); // abc.mp3
                int dotIndex = fileName.lastIndexOf('.');

                String nameWithoutExt = (dotIndex > 0) ? fileName.substring(0, dotIndex) : fileName;
                String extension = (dotIndex > 0) ? fileName.substring(dotIndex + 1) : "";

                Optional<Words> opt = wordsRepository.findActiveByName(nameWithoutExt);
                if (opt.isEmpty()) {
                    Words word = new Words(file.getName(), nameWithoutExt, extension, file.getAbsolutePath());
                    words.add(word);
                }
            }
        }
        wordsRepository.saveAll(words);
    }

    public byte[] speechSynthesis() {
        String filePath = "src/main/java/nqt/base_java_spring_be/data/Viet11K.txt";
        Path path = Paths.get(filePath);

        if (!Files.exists(path)) {
            System.err.println("Không tìm thấy file tại: " + path.toAbsolutePath());
            return null;
        }

        System.out.println("Bắt đầu quét tìm 10 từ mới...");

        try (Stream<String> lines = Files.lines(path)) {
            lines
                    .map(String::trim)                 // 1. Cắt khoảng trắng thừa
                    .filter(text -> !text.isEmpty())   // 2. Bỏ dòng trống
                    .filter(text -> {                  // 3. QUAN TRỌNG: Kiểm tra DB trước
                        // Nếu từ đã tồn tại -> trả về false (để dòng này bị loại bỏ khỏi Stream)
                        // Nếu từ chưa có -> trả về true (giữ lại để xử lý)
                        boolean exists = wordsRepository.findFirstByNameIgnoreCase(text).isPresent();
                        if (exists) {
                            // System.out.println("Bỏ qua từ đã có: " + text); // Uncomment nếu muốn debug
                        }
                        return !exists;
                    })
                    .limit(20)                         // 4. Chỉ lấy 10 từ thỏa mãn điều kiện trên
                    .forEach(textToSpeak -> {          // 5. Thực hiện xử lý cho 10 từ này
                        System.out.println(">>> Đang xử lý từ mới: " + textToSpeak);
                        try {
                            // Gọi hàm tải MP3 và lưu vào DB
                            // Lưu ý: Hàm này phải có logic lưu Words vào DB sau khi tải xong
                            // để lần chạy sau bộ lọc ở bước 3 mới hoạt động đúng.
                            speechSynthesisViettel(textToSpeak);

                            // Nghỉ 1 chút
                            Thread.sleep(5000);
                        } catch (Exception e) {
                            System.err.println("Lỗi xử lý từ: " + textToSpeak + " - " + e.getMessage());
                        }
                    });

            System.out.println("=== Hoàn tất batch 10 từ ===");

        } catch (IOException e) {
            e.printStackTrace();
        }
        return null;
    }

    public byte[] speechSynthesisViettel(String text) {
//        Optional<Words> existingWord = wordsRepository.findFirstByNameIgnoreCase(text);
//        if (existingWord.isPresent()) {
//            System.out.println("Từ đã tồn tại trong DB: " + text + " -> Bỏ qua.");
//            return null; // Kết thúc hàm, không gọi API
//        }
//
//        System.out.println("Từ chưa tồn tại, đang gọi API Viettel cho: " + text);
        // Cấu hình Header
        String token = "e1f5ac197128ebf2c8039472bffc4fc2";
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        headers.set("accept", "*/*");

        ViettelTtsRequest requestBody = ViettelTtsRequest.builder()
                .text(text)
                .voice("hn-thanhphuong") // Giọng nữ miền Bắc
                .speed(1.0f)
                .ttsReturnOption(3) // 3 = mp3
                .token(token)
                .withoutFilter(false)
                .build();

        HttpEntity<ViettelTtsRequest> entity = new HttpEntity<>(requestBody, headers);

        // Gọi API
        try {
            ResponseEntity<byte[]> response = restTemplate.exchange(
                    VIETTEL_API_URL,
                    HttpMethod.POST,
                    entity,
                    byte[].class
            );

            if (response.getStatusCode() == HttpStatus.OK && response.getBody() != null) {
                byte[] audioBytes = response.getBody();

                // 5. Cấu hình đường dẫn lưu file
                String outputDir = "D:/BackUp Db/mp3-output";
                Path dirPath = Paths.get(outputDir);

                // Tạo thư mục nếu chưa tồn tại
                if (!Files.exists(dirPath)) {
                    Files.createDirectories(dirPath);
                }

                // Tạo tên file duy nhất: viettel_tts_YYYYMMDD_HHMMSS.mp3
//                String timeStamp = LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss"));
                String fileName = text + ".mp3";
                Path filePath = dirPath.resolve(fileName);

                // 6. Ghi byte[] ra file
                Files.write(filePath, audioBytes);

                System.out.println("Đã tải file thành công tại: " + filePath.toAbsolutePath());

                try {
                    Words newWord = Words.builder()
                            .name(text)                         // Tên từ hiển thị
                            .fullName(fileName)                 // Tên file (abc.mp3)
                            .path(filePath.toAbsolutePath().toString()) // Đường dẫn tuyệt đối
                            .type("mp3")                        // Loại file
                            .build();

                    // Set active = true để lần sau tìm kiếm sẽ thấy
                    newWord.setActive(true);
                    // Nếu Entity Words của bạn có trường extension, hãy set thêm: newWord.setExtension("mp3");

                    wordsRepository.save(newWord);
                    System.out.println(">> Database Inserted: " + text);

                } catch (Exception dbEx) {
                    System.err.println("!!! LỖI LƯU DB cho từ: " + text + " -> " + dbEx.getMessage());
                    // Không throw exception ở đây để vòng lặp vẫn tiếp tục chạy từ tiếp theo
                }

                // Trả về đường dẫn file để sử dụng tiếp nếu cần
                return null;
            } else {
                throw new RuntimeException("Lỗi khi gọi Viettel AI: " + response.getStatusCode());
            }
        } catch (Exception e) {
            e.printStackTrace();
            throw new RuntimeException("Không thể kết nối tới Viettel AI");
        }
    }

    private void handleNotFoundWord(String word) {
        System.out.println("Không tìm thấy từ: " + word);
        Optional<Words> existed = wordsRepository.findFirstByNameIgnoreCase(word);
        if (existed.isEmpty()) {
            Words w = Words.builder()
                    .fullName(word)
                    .name(word)
                    .type("")
                    .path("")
                    .build();
            w.setActive(false);

            wordsRepository.save(w);
            System.out.println("Đã thêm từ mới với trạng thái inactive: " + word);
        } else {
            System.out.println("Từ đã tồn tại trong DB (inactive), không thêm mới: " + word);
        }
    }

    private List<String> tokenizeAndSegmentSentence(String sentence, TextToMp3Request.PauseConfig pauses) {
        if (sentence == null || sentence.isBlank()) {
            return Collections.emptyList();
        }

        List<String> tokens = new ArrayList<>();
        String remainingSentence = sentence; // KHÔNG lowercase để giữ nguyên dấu & spacing

        while (!remainingSentence.isEmpty()) {
            int originalLength = remainingSentence.length();
            char firstChar = remainingSentence.charAt(0);

            // 1. Nếu là khoảng trắng → tạo PAUSE từ wordPause
            if (firstChar == ' ') {
                double pause = pauses.getWordPause();
                if (pause > 0) tokens.add("PAUSE:" + pause);
                remainingSentence = remainingSentence.substring(1);
                continue;
            }

            // 2. Nếu là dấu câu → tạo PAUSE
            Double pauseSec = null;

            switch (firstChar) {
                case '.': pauseSec = pauses.getDotPause(); break;
                case ',': pauseSec = pauses.getCommaPause(); break;
                case ';': pauseSec = pauses.getSemicolonPause(); break;
                case ':': pauseSec = pauses.getColonPause(); break;
                case '?': pauseSec = pauses.getQuestionPause(); break;
                case '!': pauseSec = pauses.getExclamationPause(); break;
                case '\n':
                case '\r': pauseSec = pauses.getLineBreakPause(); break;
                case '(':
                case ')':
                case '"':
                case '“':
                case '”': pauseSec = pauses.getParenthesisPause(); break;
            }

            if (pauseSec != null && pauseSec > 0) {
                tokens.add("PAUSE:" + pauseSec);
                remainingSentence = remainingSentence.substring(1);
                continue;
            }

            // 3. Nếu bắt đầu bằng ký tự là chữ → xử lý từ / từ ghép
            if (Character.isLetterOrDigit(firstChar) || firstChar == '\'' || firstChar == '’') {

                // Tìm wordEndIndex
                int wordEndIndex = remainingSentence.length();
                for (int i = 0; i < remainingSentence.length(); i++) {
                    char c = remainingSentence.charAt(i);
                    if (!Character.isLetterOrDigit(c) && c != '\'' && c != '’' && c != ' ') {
                        wordEndIndex = i;
                        break;
                    }
                }

                String chunk = remainingSentence.substring(0, wordEndIndex);
                String[] parts = chunk.trim().split("\\s+");

                String wordToken = null;
                int tokenLength = 0;

                // 3a. Tìm từ ghép
                for (int n = Math.min(MAX_SEARCH_WORDS, parts.length); n >= 2; n--) {
                    String candidate = String.join(" ", Arrays.copyOfRange(parts, 0, n));
                    Optional<Words> opt = wordsRepository.findActiveByName(candidate.toLowerCase());
                    if (opt.isPresent()) {
                        wordToken = candidate;

                        // Tính chiều dài thực sự bao gồm khoảng trắng sau
                        Pattern p = Pattern.compile("^" + Pattern.quote(candidate));

                        Matcher m = p.matcher(remainingSentence);
                        if (m.find()) tokenLength = m.end();
                        break;
                    }
                }

                // 3b. Nếu không có từ ghép → lấy từ đơn
                if (wordToken == null) {
                    wordToken = parts[0];

                    Pattern p = Pattern.compile("^" + Pattern.quote(wordToken));
                    Matcher m = p.matcher(remainingSentence);
                    if (m.find()) tokenLength = m.end();
                    else tokenLength = wordToken.length();
                }

                // Thêm token
                tokens.add(wordToken);
                remainingSentence = remainingSentence.substring(tokenLength);
                continue;
            }

            // 4. Nếu không thuộc loại nào → bỏ qua 1 ký tự
            remainingSentence = remainingSentence.substring(1);

            // Chống infinite loop
            if (remainingSentence.length() == originalLength) {
                break;
            }
        }

        return tokens;
    }

    private void processTokenToFiles(
            String token,
            String positionType,
            List<String> inputFiles,
            List<String> notFoundWords,
            Set<Path> tempFilesToCleanUp,
            Path tmpDir) {

        // 1. Tìm token (Có thể là từ ghép hoặc từ đơn)
        Optional<Words> opt = wordsRepository.findActiveByName(token);

        if (opt.isPresent() && opt.get().getPath() != null && !opt.get().getPath().isEmpty()) {
            // 1a. TÌM THẤY: Xử lý file audio bình thường (trim & add)
            String originalPath = opt.get().getPath();
            try {
                String trimmedPath = processAudioSegment(originalPath, tmpDir, "mid");
                inputFiles.add(trimmedPath);
                tempFilesToCleanUp.add(Paths.get(trimmedPath));
            } catch (Exception e) {
                System.err.println("Lỗi khi cắt khoảng lặng cho từ: " + token + ". Bỏ qua.");
                notFoundWords.add(token);
                handleNotFoundWord(token);
            }
        } else {
            // 1b. KHÔNG TÌM THẤY: Tách thành các từ đơn để ghép lại
            String[] subWords = token.trim().split("\\s+");
            boolean anySubWordFound = false;

            for (int i = 0; i < subWords.length; i++) {
                String subWord = subWords[i];
                String subPosition = "mid";
                if (positionType.equals("start") && i == 0) subPosition = "start";
                if (positionType.equals("end") && i == subWords.length - 1) subPosition = "end";

                // Tìm từ đơn trong DB
                Optional<Words> subOpt = wordsRepository.findActiveByName(subWord);

                // Kiểm tra Path không null/rỗng
                if (subOpt.isPresent() && subOpt.get().getPath() != null && !subOpt.get().getPath().isEmpty()) {
                    anySubWordFound = true;
                    String originalPath = subOpt.get().getPath();
                    try {
                        // Trim từ đơn
                        String trimmedPath = processAudioSegment(originalPath, tmpDir, subPosition);
                        inputFiles.add(trimmedPath);
                        tempFilesToCleanUp.add(Paths.get(trimmedPath));
                    } catch (Exception e) {
                        System.err.println("Lỗi khi cắt khoảng lặng cho từ đơn: " + subWord + ". Bỏ qua.");
                    }

                    // Thêm PAUSE ngắn giữa các từ đơn để tạo âm thanh tự nhiên hơn
                    if (i < subWords.length - 1) {
                        Path silenceFile = createSilenceWavFile(COMPOUND_WORD_SPLIT_PAUSE_SEC);
                        if (silenceFile != null) {
                            inputFiles.add(silenceFile.toAbsolutePath().toString());
                            tempFilesToCleanUp.add(silenceFile);
                        }
                    }
                } else {
                    // Nếu từ đơn cũng không tìm thấy -> coi đây là lỗi cuối cùng
                    notFoundWords.add(subWord);
                    handleNotFoundWord(subWord);
                }
            }

            if (anySubWordFound) {
                System.out.println("Từ ghép không có sẵn, đã ghép từ các từ đơn: " + token);
            }
        }
    }

    // --- HÀM PUBLIC CHÍNH ---

    @Override
    public TextToMp3Result textToMp3(TextToMp3Request req) {
        String sentence = req.getWord().toLowerCase();
        String baseOutputPath = "D:/BackUp Db/mp3-output";

        if (sentence == null || sentence.trim().isEmpty()) {
            throw new IllegalArgumentException("Câu đầu vào không được rỗng.");
        }

        List<String> tokens = tokenizeAndSegmentSentence(sentence, req.getPauses());
        if (tokens.isEmpty()) {
            throw new IllegalArgumentException("Không tìm thấy token hợp lệ từ câu đầu vào.");
        }

        List<String> notFoundWords = new ArrayList<>();
        List<String> inputFiles = new ArrayList<>();
        Set<Path> tempFilesToCleanUp = new HashSet<>();

        Path tmpDir = Paths.get(baseOutputPath);

        try {
            Files.createDirectories(tmpDir);

            for (int i = 0; i < tokens.size(); i++) {
                String t = tokens.get(i);
                // A. Xử lý Pause
                if (t.startsWith("PAUSE:")) {
                    Double sec = Double.parseDouble(t.substring(6));
                    Path silenceFile = createSilenceWavFile(sec);
                    if (silenceFile != null) {
                        inputFiles.add(silenceFile.toAbsolutePath().toString());
                        tempFilesToCleanUp.add(silenceFile);
                    }
                    continue;
                }

                // B. Xác định vị trí (Logic thông minh hơn 1 chút để bỏ qua dấu câu)
                String positionType = "mid";

                // Nếu là từ đầu tiên (không tính pause đầu nếu có)
                if (i == 0 || (i == 1 && tokens.get(0).startsWith("PAUSE:"))) {
                    positionType = "start";
                }
                // Nếu là từ cuối cùng
                else if (i == tokens.size() - 1) {
                    positionType = "end";
                }

                System.out.println("positionType: " + positionType);

                // Gọi hàm xử lý
                processTokenToFiles(t, positionType, inputFiles, notFoundWords, tempFilesToCleanUp, tmpDir);
            }

            if (inputFiles.isEmpty()) {
                throw new IllegalStateException("Không có file audio nào để ghép. Vui lòng kiểm tra các từ.");
            }

            // Tạo file output cuối cùng
            String resultFileName = "tts_" + LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss_SSS")) + ".mp3";
            String resultPath = tmpDir.resolve(resultFileName).toAbsolutePath().toString();

            // Ghép file
            performFfmpegConcatFilterComplex(inputFiles, resultPath, tmpDir);

            byte[] audio = Files.readAllBytes(Paths.get(resultPath));
            return new TextToMp3Result(audio, notFoundWords);

        } catch (IOException e) {
            throw new RuntimeException("Lỗi I/O (Đọc/Ghi File)", e);
        } finally {
            // Dọn dẹp tất cả file tạm (silence và file trimmed)
            for (Path p : tempFilesToCleanUp) {
                try {
                    Files.deleteIfExists(p);
                } catch (Exception ignored) { }
            }
        }
    }
}