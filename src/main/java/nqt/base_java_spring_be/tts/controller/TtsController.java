package nqt.base_java_spring_be.tts.controller;

import jakarta.validation.constraints.NotBlank;
import nqt.base_java_spring_be.tts.service.iservices.TtsService;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/tts")
@Validated
public class TtsController {

    private final TtsService tts;

    public TtsController(TtsService tts) {
        this.tts = tts;
    }

    /**
     * GET /api/tts/vi?word=...
     * Đầu vào: 1 từ tiếng Việt (query param "word")
     * Đầu ra: trực tiếp file MP3 (audio/mpeg), có thể cache ra đĩa.
     */
    @GetMapping(value = "/vi", produces = "audio/mpeg")
    public ResponseEntity<byte[]> speakOneVi(@RequestParam @NotBlank String word) {
        byte[] mp3 = tts.synthesizeOneWordVi(word);
        String suggestedName = word.replaceAll("[^\\p{L}\\p{N}_-]", "_") + ".mp3";

        return ResponseEntity.ok()
                .contentType(MediaType.valueOf("audio/mpeg"))
                .header(HttpHeaders.CONTENT_DISPOSITION, "inline; filename=\"" + suggestedName + "\"")
                .body(mp3);
    }
}
