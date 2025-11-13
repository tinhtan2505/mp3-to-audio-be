package nqt.base_java_spring_be.tts.service.impl;

import nqt.base_java_spring_be.entity.Words;
import nqt.base_java_spring_be.repository.WordsRepository;
import nqt.base_java_spring_be.tts.azure.AzureTtsClient;
import nqt.base_java_spring_be.tts.dto.TextToMp3Request;
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

    private final AzureTtsClient client;
    private final String storageDir;

    private final WordsRepository wordsRepository;

    public TtsServiceImpl(
            AzureTtsClient client,
            @Value("${app.tts.storage-dir:}") String storageDir,
            @Value("${app.tts.ffmpeg-path:}") String ffmpegPath, // <-- add this
            WordsRepository wordsRepository) {
        this.client = client;
        this.storageDir = storageDir == null ? "" : storageDir.trim();
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

    @Override
    public byte[] synthesizeOneWordVi(String word) {
        if (word == null || word.trim().isEmpty()) {
            throw new IllegalArgumentException("word is blank");
        }
        try {
            String text = word.trim();
            String id = sha256("vi|word|" + text);
            String fileName = id + ".mp3";

            if (!storageDir.isEmpty()) {
                Path root = Paths.get(storageDir);
                Files.createDirectories(root);
                Path f = root.resolve(fileName);
                if (Files.exists(f)) {
                    return Files.readAllBytes(f); // cache hit
                }
                byte[] mp3 = client.synthesizeViMp3(text);
                Files.write(f, mp3, StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
                return mp3;
            }

            // No cache to disk
            return client.synthesizeViMp3(text);
        } catch (Exception e) {
            throw new RuntimeException(e);
        }
    }

    private String sha256(String input) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        return HexFormat.of().formatHex(md.digest(input.getBytes()));
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

                Optional<Words> opt = wordsRepository.findFirstByNameIgnoreCase(nameWithoutExt);
                if (opt.isEmpty()) {
                    Words word = new Words(file.getName(), nameWithoutExt, extension, file.getAbsolutePath());
                    words.add(word);
                }
            }
        }
        wordsRepository.saveAll(words);
    }

    @Override
    public void textToMp3(TextToMp3Request req){
//        String sentence = req.getWord(),  outputFilePath = "D:/My Project/MP3_TO_AUDIO/mp3-output";
        String sentence = "giá như chưa từng yêu chưa quan tâm nhiều về nhau",  outputFilePath = "D:/BackUp Db/word";
        if (sentence == null || sentence.trim().isEmpty()) {
            throw new IllegalArgumentException("Câu nhập vào rỗng");
        }
        List<String> words = splitToWords(sentence);
        if (words.isEmpty()) {
            throw new IllegalArgumentException("Không tìm thấy từ hợp lệ trong câu");
        }
        List<String> inputFiles = new ArrayList<>();
        for (String w : words) {
            Optional<Words> opt = wordsRepository.findFirstByNameIgnoreCase(w);
            if (opt.isPresent()) {
                inputFiles.add(opt.get().getPath());
            } else {
                // Nếu không tìm thấy, ghi log và bỏ qua (cũng có thể ném exception tùy yêu cầu)
                System.out.println("Không tìm thấy từ trong DB: " + w + " — bỏ qua");
            }
        }
        if (inputFiles.isEmpty()) {
            throw new IllegalStateException("Không có file mp3 nào tương ứng với các từ trong câu");
        }
        Path tmpDir;
        try {
            tmpDir = Files.createTempDirectory("concat_mp3_");
        } catch (IOException e) {
            throw new RuntimeException("Không thể tạo thư mục tạm", e);
        }
        tmpDir.toFile().deleteOnExit();

        // 4) Tạo file silence 0.3s bằng ffmpeg (stereo 44.1k)
        Path silenceFile = tmpDir.resolve("silence_0_3s.mp3");
        createSilenceMp3(silenceFile.toString(), 0.3);

        // 5) Lập danh sách đầu vào: word1.mp3, silence.mp3, word2.mp3, silence.mp3, ...
        List<String> orderedFiles = new ArrayList<>();
        for (int i = 0; i < inputFiles.size(); i++) {
            orderedFiles.add(inputFiles.get(i));
            if (i < inputFiles.size() - 1) {
                orderedFiles.add(silenceFile.toString());
            }
        }

        String out = performFfmpegConcatFilterComplex(orderedFiles, outputFilePath, tmpDir);

        try {
            Files.deleteIfExists(silenceFile);
            // Files.deleteIfExists(tmpDir); // không thể xóa nếu không rỗng; tmp files auto-delete on exit
        } catch (IOException e) {
            // ignore
        }

    }

    private List<String> splitToWords(String sentence) {
        // tách bằng whitespace, sau đó trim các ký tự không phải chữ/số ở đầu và cuối
        String[] parts = sentence.trim().split("\\s+");
        List<String> out = new ArrayList<>();
        for (String p : parts) {
            // loại bỏ ký tự không phải chữ/số ở đầu hoặc cuối (đảm bảo hỗ trợ unicode)
            String w = p.replaceAll("^[^\\p{L}\\p{N}]+|[^\\p{L}\\p{N}]+$", "");
            if (!w.isEmpty()) {
                out.add(w);
            }
        }
        return out;
    }

    private void createSilenceMp3(String silencePath, double seconds) {
        if (ffmpegCmdResolved == null) {
            throw new IllegalStateException("ffmpeg not available. Configure app.tts.ffmpeg-path or add ffmpeg to PATH and restart.");
        }

        List<String> cmd = Arrays.asList(
                ffmpegCmdResolved, "-y",
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t", String.valueOf(seconds),
                "-q:a", "9",
                silencePath
        );

        ProcessBuilder pb = new ProcessBuilder(cmd);
        pb.redirectErrorStream(true);
        Process p;
        try {
            p = pb.start();
        } catch (IOException e) {
            throw new RuntimeException("Không thể khởi chạy ffmpeg. Hãy kiểm tra ffmpeg đã cài và có trong PATH chưa.", e);
        }

        // đọc output (tránh block)
        try (BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
            String line;
            while ((line = br.readLine()) != null) {
                // optional logging
            }
        } catch (IOException e) {
            // lỗi đọc luồng output
            throw new RuntimeException("Lỗi đọc output của ffmpeg", e);
        }

        try {
            int rc = p.waitFor();
            if (rc != 0) {
                throw new RuntimeException("ffmpeg exit code " + rc);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new RuntimeException("Quá trình ffmpeg bị interrupt", e);
        }
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