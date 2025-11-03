package nqt.base_java_spring_be.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import nqt.base_java_spring_be.realtime.RealtimeEvents;
import nqt.base_java_spring_be.repository.ProjectRepository;
import nqt.base_java_spring_be.service.iservices.WorkAudioService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.stream.*;
import lombok.extern.slf4j.Slf4j;

@Slf4j
@Service
@RequiredArgsConstructor
@Transactional
public class WorkAudioServiceImpl implements WorkAudioService {
    private final ProjectRepository repo;
    private final RealtimeEvents realtime;
    private final ObjectMapper mapper = new ObjectMapper();

    @Override
    public void build() {
        String[] inputFiles = {
                "src/main/java/nqt/base_java_spring_be/data/tudientv.txt",
                "src/main/java/nqt/base_java_spring_be/data/hongocduc.txt",
                "src/main/java/nqt/base_java_spring_be/data/wiktionary.txt"
        };
        String outputFile = "src/main/java/nqt/base_java_spring_be/data/unique_all.txt";

        Set<String> uniqueWords = new TreeSet<>();

        for (String file : inputFiles) {
            Path path = Paths.get(file);
            if (!Files.exists(path)) {
                log.warn("⚠️ File không tồn tại: {}", file);
                continue;
            }
            try (Stream<String> lines = Files.lines(path)) {
                lines.map(String::trim)
                        .filter(line -> !line.isEmpty())
                        .flatMap(line -> {
                            try {
                                Map<?, ?> json = mapper.readValue(line, Map.class);
                                String text = (String) json.get("text");
                                if (text == null || text.isBlank()) return Stream.empty();
                                return Arrays.stream(text.trim().split("\\s+"));
                            } catch (Exception e) {
                                log.debug("Bỏ qua dòng lỗi JSON trong {}: {}", file, e.getMessage());
                                return Stream.empty();
                            }
                        })
                        .map(String::toLowerCase)
                        .filter(word -> !word.isEmpty())
                        .filter(word -> word.matches("^[\\p{L}]+$"))
                        .forEach(uniqueWords::add);
            } catch (IOException e) {
                log.error("Lỗi đọc file: {}", file, e);
            }
        }

        log.info("Tổng số từ đơn duy nhất (đã gộp 3 nguồn): {}", uniqueWords.size());
        uniqueWords.stream().limit(50).forEach(System.out::println);

        try {
            Files.write(
                    Paths.get(outputFile),
                    uniqueWords,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.TRUNCATE_EXISTING
            );
            log.info("✅ Đã ghi file hợp nhất: {}", outputFile);
        } catch (IOException e) {
            log.error("Lỗi ghi file đầu ra: {}", outputFile, e);
        }
    }

}

