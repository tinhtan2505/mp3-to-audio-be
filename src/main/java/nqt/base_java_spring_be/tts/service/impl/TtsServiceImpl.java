package nqt.base_java_spring_be.tts.service.impl;

import nqt.base_java_spring_be.entity.Words;
import nqt.base_java_spring_be.repository.WordsRepository;
import nqt.base_java_spring_be.tts.azure.AzureTtsClient;
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
import java.security.MessageDigest;
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
            System.out.println("ffmpeg resolved to: " + this.ffmpegCmdResolved);
            // debug: show PATH visible to the JVM
            System.out.println("JVM PATH=" + System.getenv("PATH"));
        } catch (RuntimeException ex) {
            System.err.println("ffmpeg not found: " + ex.getMessage());
            this.ffmpegCmdResolved = null;
        }
    }

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
            throw new RuntimeException("Configured ffmpeg path not found or not executable: " + configuredFfmpegPath);
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

        throw new RuntimeException("ffmpeg not found in PATH and no app.tts.ffmpeg-path configured");
    }

    private boolean isWindows() {
        return System.getProperty("os.name").toLowerCase().contains("win");
    }

    public void insertWords(){
//        String directoryPath = "D:/My Project/MP3_TO_AUDIO/mp3";
        String directoryPath = "D:/BackUp Db/work1";
        List<String> mp3Files = new ArrayList<>();
        File folder = new File(directoryPath);

        if (!folder.exists() || !folder.isDirectory()) {
            throw new IllegalArgumentException("Thư mục không tồn tại: " + directoryPath);
        }

        File[] files = folder.listFiles((dir, name) -> name.toLowerCase().endsWith(".mp3"));
        List<Words> words = new ArrayList<>();
        if (files != null) {
            for (File file : files) {
//                String oldName = file.getName();
//                String fileNameWithoutExt = oldName.substring(0, oldName.length() - 4);
//                String newName = fileNameWithoutExt;
//                if (fileNameWithoutExt.contains("_")) {
//                    newName = fileNameWithoutExt.substring(0, fileNameWithoutExt.indexOf("_"));
//                }
//
//                if (!newName.equals(fileNameWithoutExt)) {
//                    File newFile = new File(file.getParent(), newName + ".mp3");
//                    boolean success = file.renameTo(newFile);
//                }
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

    private Path createSilenceMp3File(Double durationSec) {
        if (durationSec == null || durationSec <= 0) return null;

        if (ffmpegCmdResolved == null) {
            throw new IllegalStateException("ffmpeg not available.");
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
                throw new RuntimeException("ffmpeg exit " + rc + " when creating silence WAV.");
            }

            return tmpFile;

        } catch (Exception e) {
            throw new RuntimeException("Error creating silence WAV: " + durationSec + "s", e);
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

            // 2. Nếu là dấu câu → tạo PAUSE như splitToTokens()
            Double pauseSec = null;

            switch (firstChar) {
//                case ' ': pauseSec = pauses.getWordPause(); break;
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


    @Override
    public TextToMp3Result textToMp3(TextToMp3Request req) {
        String sentence = req.getWord().toLowerCase();
//        String baseOutputPath = "D:/My Project/MP3_TO_AUDIO/mp3-output";
        String baseOutputPath = "D:/BackUp Db/mp3-output";

        if (sentence == null || sentence.trim().isEmpty()) {
            throw new IllegalArgumentException("Câu nhập vào rỗng");
        }

        List<String> tokens = tokenizeAndSegmentSentence(sentence, req.getPauses());
        if (tokens.isEmpty()) {
            throw new IllegalArgumentException("Không tìm thấy token hợp lệ");
        }

        List<String> notFoundWords = new ArrayList<>();
        List<String> inputFiles = new ArrayList<>();
        Set<Path> tempFilesToCleanUp = new HashSet<>(); // Set để lưu tất cả file tạm (silence + trimmed)

        Path tmpDir = Paths.get(baseOutputPath);

        try {
            Files.createDirectories(tmpDir);

            for (String t : tokens) {
                // A. Xử lý Pause
                if (t.startsWith("PAUSE:")) {
                    Double sec = Double.parseDouble(t.substring(6));
                    // createSilenceMp3File hiện tại đang tạo .wav (như bạn đã code ở trên), rất tốt.
                    Path silenceFile = createSilenceMp3File(sec);
                    if (silenceFile != null) {
                        inputFiles.add(silenceFile.toAbsolutePath().toString());
                        tempFilesToCleanUp.add(silenceFile);
                    }
                    continue;
                }

                // B. Xử lý Từ
                Optional<Words> opt = wordsRepository.findActiveByName(t);
                if (opt.isPresent()) {
                    String originalPath = opt.get().getPath();
                    // ==> GỌI TRIM SILENCE TẠI ĐÂY <==
                    // Tạo file trimmed .wav trong thư mục tạm
                    String trimmedPath = trimSilence(originalPath, tmpDir);

                    inputFiles.add(trimmedPath);
                    tempFilesToCleanUp.add(Paths.get(trimmedPath)); // Đánh dấu để xóa sau khi xong
                } else {
                    notFoundWords.add(t);
                    handleNotFoundWord(t);
                }
            }

            if (inputFiles.isEmpty()) {
                throw new IllegalStateException("Không có file audio nào để ghép");
            }

            // Tạo file output cuối cùng
            String resultFileName = "tts_" + LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss_SSS")) + ".mp3";
            String resultPath = tmpDir.resolve(resultFileName).toAbsolutePath().toString();

            // Ghép file
            performFfmpegConcatFilterComplex(inputFiles, resultPath, tmpDir);

            byte[] audio = Files.readAllBytes(Paths.get(resultPath));
            return new TextToMp3Result(audio, notFoundWords);

        } catch (IOException e) {
            throw new RuntimeException("IO Error", e);
        } finally {
            // Dọn dẹp tất cả file tạm (silence và file trimmed)
            for (Path p : tempFilesToCleanUp) {
                try {
                    Files.deleteIfExists(p);
                } catch (Exception ignored) { }
            }
        }
    }

    private List<String> splitToTokens(String sentence, TextToMp3Request.PauseConfig pauses) {
        List<String> tokens = new ArrayList<>();

        for (int i = 0; i < sentence.length(); i++) {
            char c = sentence.charAt(i);

            // Nếu là ký tự tạo thành một từ
            if (Character.isLetterOrDigit(c) || c == '\'' || c == '’') {
                StringBuilder sb = new StringBuilder();
                while (i < sentence.length() &&
                        (Character.isLetterOrDigit(sentence.charAt(i)) ||
                                sentence.charAt(i) == '\'' || sentence.charAt(i) == '’')) {
                    sb.append(sentence.charAt(i));
                    i++;
                }
                i--;
                tokens.add(sb.toString());
                continue;
            }

            // Xử lý các dấu cần pause
            Double pauseSec = null;

            switch (c) {
                case ' ': pauseSec = pauses.getWordPause(); break;
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
            }
        }

        return tokens;
    }

    private String performFfmpegConcatFilterComplex(List<String> inputs, String outputFilePath, Path workingDir) {
        if (inputs == null || inputs.isEmpty()) throw new IllegalArgumentException("No input files");
        if (ffmpegCmdResolved == null) throw new IllegalStateException("ffmpeg not available");

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
            // Input: list file
            // Output: encode sang MP3
            List<String> cmd = new ArrayList<>();
            cmd.add(ffmpegCmdResolved);
            cmd.add("-y");
            cmd.add("-f"); cmd.add("concat");
            cmd.add("-safe"); cmd.add("0");
            cmd.add("-i"); cmd.add(listFile.toAbsolutePath().toString());

            // Encoding settings cho MP3
            cmd.add("-c:a"); cmd.add("libmp3lame"); // Bắt buộc encode lại
            cmd.add("-b:a"); cmd.add("192k");       // Bitrate cao
            cmd.add("-ar"); cmd.add("44100");       // Sample rate
            cmd.add("-ac"); cmd.add("2");           // Stereo

            // Tối ưu hóa nối file để giảm glitch
            cmd.add("-avoid_negative_ts"); cmd.add("make_zero");

            cmd.add(outputFilePath);

            ProcessBuilder pb = new ProcessBuilder(cmd);
            if (workingDir != null) pb.directory(workingDir.toFile());
            pb.redirectErrorStream(true);

            Process p = pb.start();

            // Log output (quan trọng để debug)
            try (BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                String line;
                while ((line = br.readLine()) != null) {
                    // System.out.println("[FFMPEG] " + line); // Uncomment để debug
                }
            }

            int rc = p.waitFor();
            Files.deleteIfExists(listFile); // Xóa file list tạm

            if (rc != 0) {
                throw new RuntimeException("FFmpeg concat failed, exit=" + rc);
            }

            return outputFilePath;

        } catch (Exception e) {
            throw new RuntimeException("Error during concat", e);
        }
    }

    private String trimSilence(String inputPath, Path tempDir) {
        if (ffmpegCmdResolved == null) throw new IllegalStateException("FFmpeg not found");

        String fileName = new File(inputPath).getName();
        // Tạo file tạm .wav (quan trọng: dùng wav để tránh padding của mp3)
        String outputFileName = "trimmed_" + UUID.randomUUID().toString() + "_" + fileName + ".wav";
        Path outputPath = tempDir.resolve(outputFileName);

        // Filter:
        // start_periods=1: stop_periods=1: detection_threshold=-50dB
        // start_duration=0: stop_duration=0
        // remove silence from start AND end
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

            // (Optional) Đọc log nếu cần debug, nếu không thì gobble để tránh deadlock
            new Thread(new StreamGobbler(p.getInputStream(), s -> {})).start();

            int rc = p.waitFor();
            if (rc != 0) {
                throw new RuntimeException("Trim silence failed, exit=" + rc);
            }
            return outputPath.toAbsolutePath().toString();
        } catch (Exception e) {
            throw new RuntimeException("Error trimming silence", e);
        }
    }
}