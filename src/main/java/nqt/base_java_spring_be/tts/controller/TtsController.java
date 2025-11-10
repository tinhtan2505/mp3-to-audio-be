package nqt.base_java_spring_be.tts.controller;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import lombok.extern.slf4j.Slf4j;
import nqt.base_java_spring_be.dto.CustomResponse;
import nqt.base_java_spring_be.dto.request.ProjectCreateRequest;
import nqt.base_java_spring_be.entity.Project;
import nqt.base_java_spring_be.tts.dto.TextToMp3Request;
import nqt.base_java_spring_be.tts.service.iservices.TtsService;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;

import java.net.URI;

@Slf4j
@RestController
@RequestMapping("/api/tts")
@Validated
public class TtsController {

    private final TtsService tts;

    public TtsController(TtsService tts) {
        this.tts = tts;
    }

    @GetMapping(value = "/vi", produces = "audio/mpeg")
    public ResponseEntity<byte[]> speakOneVi(@RequestParam @NotBlank String word) {
        try {
            byte[] mp3 = tts.synthesizeOneWordVi(word);
            if (mp3 == null || mp3.length == 0) {
                throw new IllegalStateException("Không tạo được audio (mp3 rỗng).");
            }
            String suggestedName = word.replaceAll("[^\\p{L}\\p{N}_-]", "_") + ".mp3";
            return ResponseEntity.ok()
                    .contentType(MediaType.valueOf("audio/mpeg"))
                    .header(HttpHeaders.CONTENT_DISPOSITION, "inline; filename=\"" + suggestedName + "\"")
                    .body(mp3);
        } catch (IllegalArgumentException e) {
            // lỗi đầu vào -> 400
            log.warn("Bad request /api/tts/vi word='{}': {}", word, e.getMessage());
            throw e;
        } catch (Exception e) {
            // lỗi hệ thống -> 500
            log.error("TTS failed for word='{}'", word, e);
            throw new RuntimeException("Tạo âm thanh thất bại: " + e.getMessage(), e);
        }
    }

    @PostMapping("/vi/text-to-mp3")
    public ResponseEntity<CustomResponse<?>> create(@Valid @RequestBody TextToMp3Request req) {
        try {
            tts.textToMp3(req);
            return ResponseEntity.ok(CustomResponse.success(null, "Thành công"));
        } catch (IllegalArgumentException e) {
            // lỗi đầu vào -> 400
            log.warn(e.getMessage());
            throw e;
        } catch (Exception e) {
            // lỗi hệ thống -> 500
            log.error("TTS failed for word='", e);
            throw new RuntimeException("Thất bại: " + e.getMessage(), e);
        }
    }
}
