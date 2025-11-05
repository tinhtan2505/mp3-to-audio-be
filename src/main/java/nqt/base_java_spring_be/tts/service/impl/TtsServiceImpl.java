package nqt.base_java_spring_be.tts.service.impl;

import nqt.base_java_spring_be.tts.azure.AzureTtsClient;
import nqt.base_java_spring_be.tts.service.iservices.TtsService;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.nio.file.*;
import java.security.MessageDigest;
import java.util.HexFormat;

@Service
public class TtsServiceImpl implements TtsService {

    private final AzureTtsClient client;
    private final String storageDir;

    public TtsServiceImpl(
            AzureTtsClient client,
            @Value("${app.tts.storage-dir:}") String storageDir) {
        this.client = client;
        this.storageDir = storageDir == null ? "" : storageDir.trim();
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
}