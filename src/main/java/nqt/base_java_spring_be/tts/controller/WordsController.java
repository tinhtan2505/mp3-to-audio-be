package nqt.base_java_spring_be.tts.controller;

import lombok.extern.slf4j.Slf4j;
import nqt.base_java_spring_be.dto.CustomResponse;
import nqt.base_java_spring_be.tts.service.iservices.WordsService;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Slf4j
@RestController
@RequestMapping("/api/words")
@Validated
public class WordsController {

    private final WordsService wordsService;

    public WordsController(WordsService wordsService) {
        this.wordsService = wordsService;
    }

    @PostMapping("/vi/insert-words")
    public ResponseEntity<CustomResponse<?>> insertWords() {
        try {
            wordsService.insertWords();
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

    @PostMapping("/vi/speech-synthesis")
    public ResponseEntity<CustomResponse<?>> speechSynthesis() {
        try {
            byte[] data = wordsService.speechSynthesis();
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
