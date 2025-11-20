package nqt.base_java_spring_be.tts.service.impl;

import nqt.base_java_spring_be.entity.Words;
import nqt.base_java_spring_be.repository.WordsRepository;
import nqt.base_java_spring_be.tts.dto.TextToMp3Request;
import nqt.base_java_spring_be.tts.dto.TextToMp3Result;
import nqt.base_java_spring_be.tts.service.iservices.TtsService;
import nqt.base_java_spring_be.utils.StreamGobbler;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

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

@Service
public class TtsServiceImpl implements TtsService {
    private final String configuredFfmpegPath;
    private String ffmpegCmdResolved;
    private static final int MAX_SEARCH_WORDS = 2;
    private static final double COMPOUND_WORD_SPLIT_PAUSE_SEC = 0.01;

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

    private Path createSilenceMp3File(Double durationSec) {
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

    private String trimSilence(String inputPath, Path tempDir) {

        if (inputPath == null || inputPath.isBlank()) {
            throw new IllegalArgumentException("Đường dẫn file audio đầu vào không được rỗng hoặc null.");
        }
        if (tempDir == null) {
            throw new IllegalArgumentException("Đường dẫn thư mục tạm thời không được null.");
        }

        if (ffmpegCmdResolved == null) throw new IllegalStateException("Không tìm thấy FFmpeg.");

        String fileName = new File(inputPath).getName();
        // Tạo file tạm .wav (quan trọng: dùng wav để tránh padding của mp3)
        String outputFileName = "trimmed_" + UUID.randomUUID().toString() + "_" + fileName + ".wav";
        Path outputPath = tempDir.resolve(outputFileName);

        // Filter:
        String filter = "silenceremove=start_periods=1:start_duration=0:start_threshold=-50dB:stop_periods=1:stop_duration=0:stop_threshold=-50dB";

        List<String> cmd = Arrays.asList(
                ffmpegCmdResolved, "-y",
                "-i", inputPath,
                "-af", filter,
                "-c:a", "pcm_s16le", // Convert sang WAV PCM
                "-ar", "44100",      // Chuẩn hóa sample rate
                "-ac", "2",          // Chuẩn hóa stereo
                outputPath.toAbsolutePath().toString()
        );

        try {
            ProcessBuilder pb = new ProcessBuilder(cmd);
            pb.redirectErrorStream(true);
            Process p = pb.start();

            // Sử dụng StreamGobbler để đọc luồng output/error của tiến trình
            new Thread(new StreamGobbler(p.getInputStream(), s -> {})).start();

            int rc = p.waitFor();
            if (rc != 0) {
                Files.deleteIfExists(outputPath);
                throw new RuntimeException("Cắt khoảng lặng thất bại, mã thoát=" + rc + ". Lệnh: " + String.join(" ", cmd));
            }
            return outputPath.toAbsolutePath().toString();
        } catch (Exception e) {
            throw new RuntimeException("Lỗi trong quá trình cắt khoảng lặng cho đường dẫn: " + inputPath, e);
        }
    }

    private String performFfmpegConcatFilterComplex(List<String> inputs, String outputFilePath, Path workingDir) {
        if (inputs == null || inputs.isEmpty()) throw new IllegalArgumentException("Không có file đầu vào để ghép.");
        if (ffmpegCmdResolved == null) throw new IllegalStateException("ffmpeg không khả dụng.");

        try {
            // 1. Tạo file list cho concat demuxer
            Path listFile = Files.createTempFile("ffmpeg_concat_", ".txt");
            StringBuilder sb = new StringBuilder();
            for (String in : inputs) {
                // Escape đường dẫn cho an toàn
                String safePath = in.replace("'", "'\\''");
                sb.append("file '").append(safePath).append("'\n");
            }
            Files.write(listFile, sb.toString().getBytes());

            // 2. Chuẩn bị lệnh FFmpeg
            List<String> cmd = new ArrayList<>();
            cmd.add(ffmpegCmdResolved);
            cmd.add("-y");
            cmd.add("-f"); cmd.add("concat");
            cmd.add("-safe"); cmd.add("0");
            cmd.add("-i"); cmd.add(listFile.toAbsolutePath().toString());

            // Encoding settings cho MP3
            cmd.add("-c:a"); cmd.add("libmp3lame");
            cmd.add("-b:a"); cmd.add("192k");
            cmd.add("-ar"); cmd.add("44100");
            cmd.add("-ac"); cmd.add("2");

            cmd.add("-avoid_negative_ts"); cmd.add("make_zero");

            cmd.add(outputFilePath);

            ProcessBuilder pb = new ProcessBuilder(cmd);
            if (workingDir != null) pb.directory(workingDir.toFile());
            pb.redirectErrorStream(true);

            Process p = pb.start();

            // Log output
            try (BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                String line;
                while ((line = br.readLine()) != null) {
                    // System.out.println("[FFMPEG] " + line);
                }
            }

            int rc = p.waitFor();
            Files.deleteIfExists(listFile);

            if (rc != 0) {
                throw new RuntimeException("Ghép file FFmpeg thất bại, mã thoát=" + rc);
            }

            return outputFilePath;

        } catch (Exception e) {
            throw new RuntimeException("Lỗi trong quá trình ghép file", e);
        }
    }

    // --- HÀM XỬ LÝ DATABASE & TOKENIZATION ---

    public void insertWords(){
        String directoryPath = "D:/BackUp Db/work1";
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
            List<String> inputFiles,
            List<String> notFoundWords,
            Set<Path> tempFilesToCleanUp,
            Path tmpDir) {

        // 1. Tìm token (Có thể là từ ghép hoặc từ đơn)
        Optional<Words> opt = wordsRepository.findActiveByName(token);

        // Kiểm tra Path không null/rỗng để tránh lỗi trimSilence
        if (opt.isPresent() && opt.get().getPath() != null && !opt.get().getPath().isEmpty()) {
            // 1a. TÌM THẤY: Xử lý file audio bình thường (trim & add)
            String originalPath = opt.get().getPath();
            try {
                String trimmedPath = trimSilence(originalPath, tmpDir);
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

                // Tìm từ đơn trong DB
                Optional<Words> subOpt = wordsRepository.findActiveByName(subWord);

                // Kiểm tra Path không null/rỗng
                if (subOpt.isPresent() && subOpt.get().getPath() != null && !subOpt.get().getPath().isEmpty()) {
                    anySubWordFound = true;
                    String originalPath = subOpt.get().getPath();
                    try {
                        // Trim từ đơn
                        String trimmedPath = trimSilence(originalPath, tmpDir);
                        inputFiles.add(trimmedPath);
                        tempFilesToCleanUp.add(Paths.get(trimmedPath));
                    } catch (Exception e) {
                        System.err.println("Lỗi khi cắt khoảng lặng cho từ đơn: " + subWord + ". Bỏ qua.");
                    }

                    // Thêm PAUSE ngắn giữa các từ đơn để tạo âm thanh tự nhiên hơn
                    if (i < subWords.length - 1) {
                        Path silenceFile = createSilenceMp3File(COMPOUND_WORD_SPLIT_PAUSE_SEC);
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

            for (String t : tokens) {
                // A. Xử lý Pause
                if (t.startsWith("PAUSE:")) {
                    Double sec = Double.parseDouble(t.substring(6));
                    Path silenceFile = createSilenceMp3File(sec);
                    if (silenceFile != null) {
                        inputFiles.add(silenceFile.toAbsolutePath().toString());
                        tempFilesToCleanUp.add(silenceFile);
                    }
                    continue;
                }

                // B. Xử lý Từ / Từ ghép (bao gồm logic fallback)
                processTokenToFiles(t, inputFiles, notFoundWords, tempFilesToCleanUp, tmpDir);
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