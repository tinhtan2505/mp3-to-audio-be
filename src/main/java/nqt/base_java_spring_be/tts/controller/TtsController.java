package nqt.base_java_spring_be.tts.controller;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotBlank;
import lombok.extern.slf4j.Slf4j;
import nqt.base_java_spring_be.dto.CustomResponse;
import nqt.base_java_spring_be.dto.request.ProjectCreateRequest;
import nqt.base_java_spring_be.entity.Project;
import nqt.base_java_spring_be.tts.dto.TextToMp3Request;
import nqt.base_java_spring_be.tts.dto.TextToMp3Result;
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

    @PostMapping("/vi/insert-words")
    public ResponseEntity<CustomResponse<?>> insertWords() {
        try {
            tts.insertWords();
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

    @PostMapping("/vi/text-to-mp3")
    public ResponseEntity<CustomResponse<?>> textToMp3(@Valid @RequestBody TextToMp3Request req) {
        try {
            TextToMp3Result data = tts.textToMp3(req);
            return ResponseEntity.ok(CustomResponse.success(data, "Thành công"));
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
