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
        String directoryPath = "D:/BackUp Db/work";
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
            throw new IllegalStateException("ffmpeg not available. Configure app.tts.ffmpeg-path or add ffmpeg to PATH and restart.");
        }

        try {
            // Sử dụng file tạm có đuôi .mp3 để FFmpeg không nhầm lẫn
            Path tmpFile = Files.createTempFile("sil_", ".mp3");
            tmpFile.toFile().deleteOnExit(); // Đánh dấu xóa khi JVM thoát

            List<String> cmd = Arrays.asList(
                    ffmpegCmdResolved, "-y",
                    "-f", "lavfi",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-t", String.valueOf(durationSec),
                    "-q:a", "9",
                    tmpFile.toAbsolutePath().toString()
            );

            // Tái sử dụng logic thực thi FFmpeg (tạo hàm runFfmpegCommand nếu cần)
            Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
            int rc = p.waitFor();

            if (rc != 0) {
                // Đảm bảo xóa file tạm nếu tạo thất bại
                Files.deleteIfExists(tmpFile);
                throw new RuntimeException("ffmpeg exit code " + rc + " khi tạo silence.");
            }

            return tmpFile;
        } catch (Exception e) {
            throw new RuntimeException("Lỗi tạo silence với độ dài " + durationSec + " giây", e);
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

        if (sentence == null || sentence.isBlank()) {
            return Collections.emptyList();
        }

        List<String> tokens = new ArrayList<>();
        String remainingSentence = sentence.toLowerCase(); // Giữ nguyên case ban đầu cho việc cắt chuỗi, nhưng tìm kiếm bằng lowercase

        while (!remainingSentence.isEmpty()) {
            int originalLength = remainingSentence.length();

            // 1. Bỏ qua khoảng trắng đầu tiên
            remainingSentence = remainingSentence.trim();
            if (remainingSentence.isEmpty()) break;

            char firstChar = remainingSentence.charAt(0);

            // 2. Xử lý Dấu Câu (tạo PAUSE)
            if (!Character.isLetterOrDigit(firstChar) && firstChar != '\'' && firstChar != '’') {
                Double pauseSec = null;

                switch (firstChar) {
//                    case ' ': pauseSec = pauses.getWordPause(); break;
                    case '.': pauseSec = pauses.getDotPause(); break;
                    case ',': pauseSec = pauses.getCommaPause(); break;
                    case ';': pauseSec = pauses.getSemicolonPause(); break;
                    case ':': pauseSec = pauses.getColonPause(); break;
                    case '?': pauseSec = pauses.getQuestionPause(); break;
                    case '!': pauseSec = pauses.getExclamationPause(); break;
                    case '\n': case '\r': pauseSec = pauses.getLineBreakPause(); break;
                    case '(': case ')': case '"': case '“': case '”': pauseSec = pauses.getParenthesisPause(); break;
                    default: break;
                }

                if (pauseSec != null && pauseSec > 0) {
                    tokens.add("PAUSE:" + pauseSec);
                }

                // Di chuyển qua ký tự dấu câu đã xử lý
                remainingSentence = remainingSentence.substring(1).trim();
                continue;
            }

            // 3. Xử lý Từ/Từ Ghép

            // Tìm vị trí của khoảng trắng hoặc dấu câu đầu tiên để xác định giới hạn từ/cụm từ
            int wordEndIndex = remainingSentence.length();
            for (int i = 0; i < remainingSentence.length(); i++) {
                char c = remainingSentence.charAt(i);
                if (!Character.isLetterOrDigit(c) && c != '\'' && c != '’' && c != ' ') {
                    wordEndIndex = i;
                    break;
                }
            }

            // Phần chuỗi chỉ chứa từ và khoảng trắng
            String currentWordChunk = remainingSentence.substring(0, wordEndIndex).trim();
            String[] parts = currentWordChunk.split("\\s+");

            String wordToken = null;
            int wordLengthInChars = 0; // Chiều dài của token (bao gồm khoảng trắng)

            // 3a. Ưu tiên tìm Từ Ghép
            for (int n = Math.min(MAX_SEARCH_WORDS, parts.length); n >= 2; n--) {
                String multiWordCandidate = String.join(" ", Arrays.copyOfRange(parts, 0, n));
                Optional<Words> opt = wordsRepository.findActiveByName(multiWordCandidate);

                if (opt.isPresent()) {
                    wordToken = multiWordCandidate;
                    wordLengthInChars = multiWordCandidate.length();
                    // Đảm bảo token được cắt đúng với khoảng trắng theo sau
                    java.util.regex.Pattern p = java.util.regex.Pattern.compile("^" + java.util.regex.Pattern.quote(wordToken) + "\\s*");
                    java.util.regex.Matcher m = p.matcher(remainingSentence);
                    if(m.find()) {
                        wordLengthInChars = m.end();
                    }
                    break;
                }
            }

            // 3b. Nếu không tìm thấy từ ghép, lấy từ đơn đầu tiên
            if (wordToken == null && parts.length > 0) {
                wordToken = parts[0];
                // Chiều dài từ đơn (cộng khoảng trắng sau nếu có)
                java.util.regex.Pattern p = java.util.regex.Pattern.compile("^" + java.util.regex.Pattern.quote(wordToken) + "\\s*");
                java.util.regex.Matcher m = p.matcher(remainingSentence);
                if(m.find()) {
                    wordLengthInChars = m.end();
                } else {
                    wordLengthInChars = wordToken.length();
                }
            }

            // 3c. Thêm token từ/từ ghép đã tìm thấy
            if (wordToken != null && !wordToken.isBlank()) {
                tokens.add(wordToken);
                remainingSentence = remainingSentence.substring(wordLengthInChars).trim();
            } else {
                // Nếu không tìm thấy từ nào và còn ký tự không phải dấu câu/khoảng trắng, thoát
                remainingSentence = remainingSentence.substring(1).trim();
            }

            // Guardrail chống vòng lặp vô hạn
            if (remainingSentence.length() == originalLength) {
                System.err.println("Lỗi phân tích cú pháp: Không thể tiến triển từ: " + remainingSentence);
                break;
            }
        }

        return tokens;
    }

    @Override
    public TextToMp3Result textToMp3(TextToMp3Request req){
        String sentence = req.getWord().toLowerCase();
//        String baseOutputPath = "D:/BackUp Db/word";
        String baseOutputPath = "D:/My Project/MP3_TO_AUDIO/mp3-output";

        if (sentence == null || sentence.trim().isEmpty()) {
            throw new IllegalArgumentException("Câu nhập vào rỗng");
        }

//        List<String> tokens = tokenizeAndSegmentSentence(sentence, req.getPauses());
        List<String> tokens = splitToTokens(sentence, req.getPauses());

        if (tokens.isEmpty()) {
            throw new IllegalArgumentException("Không tìm thấy từ hợp lệ hoặc dấu câu để tạo token");
        }

        List<String> notFoundWords = new ArrayList<>();
        List<String> inputFiles = new ArrayList<>();

        // Sử dụng Set để lưu trữ các file silence tạm cần xóa
        Set<Path> tempSilenceFiles = new HashSet<>();

        Path tmpDir = null; // Khởi tạo bên ngoài try để cleanup

        try {
            // 1. Chuẩn bị thư mục tạm và file silence mặc định
            tmpDir = Paths.get(baseOutputPath); // Giữ nguyên cách sử dụng dir này theo code gốc
            Files.createDirectories(tmpDir);

            // 2. Thu thập Input Files và tạo file Silence động
            for (String t : tokens) {
                // A) Nếu là Pause → tạo silence file
                if (t.startsWith("PAUSE:")) {
                    Double sec = Double.parseDouble(t.substring(6));
                    Path silenceFile = createSilenceMp3File(sec);
                    if (silenceFile != null) {
                        inputFiles.add(silenceFile.toAbsolutePath().toString());
                        tempSilenceFiles.add(silenceFile);
                    }
                    continue;
                }

                // B) Nếu là từ → tìm mp3 trong DB
                Optional<Words> opt = wordsRepository.findActiveByName(t);
                if (opt.isPresent()) {
                    inputFiles.add(opt.get().getPath());
                } else {
                    notFoundWords.add(t);
                    // *Refactor: Tách logic xử lý từ không tìm thấy thành hàm riêng*
                    handleNotFoundWord(t);
                }
            }

            if (inputFiles.isEmpty()) {
                throw new IllegalStateException("Không có file mp3 nào tương ứng với các từ trong câu");
            }

            // 3. Xử lý Output Path
            // Tạo tên file đầu ra duy nhất trong thư mục tmpDir/baseOutputPath
            String resultFileName = "tts_" + LocalDateTime.now()
                    .format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss_SSS")) + ".mp3";
            String resultPath = tmpDir.resolve(resultFileName).toAbsolutePath().toString();

            // 4. Ghép file bằng FFmpeg
            performFfmpegConcatFilterComplex(inputFiles, resultPath, tmpDir);

            // 5. Đọc kết quả và trả về
            byte[] audio = Files.readAllBytes(Paths.get(resultPath));
            return new TextToMp3Result(audio, notFoundWords);
        } catch (IOException e) {
            throw new RuntimeException("Lỗi I/O trong quá trình tạo/đọc file MP3", e);
        } finally {
            // 6. Dọn dẹp: Xóa tất cả các file silence tạm đã tạo
            for (Path p : tempSilenceFiles) {
                try {
                    Files.deleteIfExists(p);
                } catch (Exception ignored) {
                    System.err.println("Không thể xóa file tạm: " + p);
                }
            }

            // *Lưu ý: Bạn đã thay đổi logic để tạo output file trong tmpDir, nên cần xóa file output nếu không muốn lưu*
            // Giả định: File kết quả (resultPath) được tạo trong D:/BackUp Db/word và được LƯU LẠI
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
        if (inputs == null || inputs.isEmpty()) {
            throw new IllegalArgumentException("No input files");
        }
        if (ffmpegCmdResolved == null) {
            throw new IllegalStateException("ffmpeg not available. Configure app.tts.ffmpeg-path or add ffmpeg to PATH and restart.");
        }

        // Ensure outputFilePath is a file with .mp3 extension (not a directory)
        Path outPath = Paths.get(outputFilePath);
        if (Files.isDirectory(outPath) || !outputFilePath.toLowerCase().endsWith(".mp3")) {
            String fileName = "tts_" + LocalDateTime.now()
                    .format(DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss")) + ".mp3";

            if (Files.isDirectory(outPath)) {
                outPath = outPath.resolve(fileName);
            } else {
                outPath = Paths.get(outputFilePath + ".mp3");
            }

            outputFilePath = outPath.toAbsolutePath().toString();
        }


        List<String> cmd = new ArrayList<>();
        cmd.add(ffmpegCmdResolved);
        cmd.add("-y");
        for (String in : inputs) {
            cmd.add("-i");
            cmd.add(in);
        }

        // Build filter_complex that normalizes each input to stereo 44100 and then concat
        // Example: [0:0]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[a0];
        //          [1:0]aformat=... [a1]; [a0][a1]concat=n=2:v=0:a=1[out]
        StringBuilder fb = new StringBuilder();
        for (int i = 0; i < inputs.size(); i++) {
            fb.append("[").append(i).append(":0]").append("aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo")
                    .append("[a").append(i).append("];");
        }
        for (int i = 0; i < inputs.size(); i++) {
            fb.append("[a").append(i).append("]");
        }
        fb.append("concat=n=").append(inputs.size()).append(":v=0:a=1[out]");

        cmd.add("-filter_complex");
        cmd.add(fb.toString());
        cmd.add("-map");
        cmd.add("[out]");
        cmd.add("-c:a");
        cmd.add("libmp3lame");
        cmd.add("-b:a");
        cmd.add("192k");
        cmd.add(outputFilePath);

        ProcessBuilder pb = new ProcessBuilder(cmd);
        if (workingDir != null) pb.directory(workingDir.toFile());
        pb.redirectErrorStream(false);

        Process p;
        try {
            p = pb.start();
        } catch (IOException e) {
            throw new RuntimeException("Không thể khởi chạy ffmpeg. Chi tiết: " + e.getMessage(), e);
        }

        StringBuilder outLog = new StringBuilder();
        StringBuilder errLog = new StringBuilder();

        Thread tOut = new Thread(new StreamGobbler(p.getInputStream(), line -> {
            outLog.append(line).append(System.lineSeparator());
        }));
        Thread tErr = new Thread(new StreamGobbler(p.getErrorStream(), line -> {
            errLog.append(line).append(System.lineSeparator());
        }));
        tOut.start();
        tErr.start();

        try {
            int rc = p.waitFor();
            tOut.join();
            tErr.join();

            if (rc != 0) {
                String msg = "ffmpeg concat (filter_complex) thất bại, exit code=" + rc
                        + "\nSTDOUT:\n" + outLog.toString()
                        + "\nSTDERR:\n" + errLog.toString();
                throw new RuntimeException(msg);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("Quá trình ffmpeg bị interrupt", e);
        }

        return outputFilePath;
    }

}