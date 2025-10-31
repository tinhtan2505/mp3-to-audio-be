package nqt.base_java_spring_be.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import nqt.base_java_spring_be.realtime.RealtimeEvents;
import nqt.base_java_spring_be.repository.ProjectRepository;
import nqt.base_java_spring_be.service.iservices.WorkAudioService;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.io.*;
import java.nio.file.*;
import java.util.*;
import java.util.stream.*;

@Service
@RequiredArgsConstructor
@Transactional
public class WorkAudioServiceImpl implements WorkAudioService {
    private final ProjectRepository repo;
    private final RealtimeEvents realtime;
    private final ObjectMapper mapper = new ObjectMapper();

    @Override
    public void build() {
        try {
            Path path = Paths.get("tudientv.txt"); // đường dẫn file txt
            Set<String> uniqueWords = Files.lines(path)
                    .map(String::trim)
                    .filter(line -> !line.isEmpty())
                    .flatMap(line -> {
                        try {
                            Map<?, ?> json = mapper.readValue(line, Map.class);
                            String text = (String) json.get("text");
                            if (text == null || text.isBlank()) return Stream.empty();
                            // tách theo khoảng trắng
                            return Arrays.stream(text.trim().split("\\s+"));
                        } catch (Exception e) {
                            return Stream.empty();
                        }
                    })
                    .map(String::toLowerCase)
                    .filter(word -> word.length() > 0)
                    .collect(Collectors.toCollection(TreeSet::new)); // TreeSet để auto sort + loại trùng

            System.out.println("Tổng số từ đơn duy nhất: " + uniqueWords.size());
            // In ra thử 50 từ đầu
            uniqueWords.stream().limit(50).forEach(System.out::println);

            // Nếu muốn lưu lại
            Files.write(Paths.get("unique_words.txt"),
                    uniqueWords,
                    StandardOpenOption.CREATE,
                    StandardOpenOption.TRUNCATE_EXISTING);

        } catch (IOException e) {
            e.printStackTrace();
        }
    }

    private String getActorUsername() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth == null) return "system";
        Object principal = auth.getPrincipal();
        if (principal instanceof nqt.base_java_spring_be.authentication.dto.UserPrincipal up) return up.getUsername();
        if (principal instanceof org.springframework.security.core.userdetails.UserDetails ud) return ud.getUsername();
        return auth.getName();
    }
}

