package nqt.base_java_spring_be.tts.controller;

import jakarta.validation.Valid;
import lombok.extern.slf4j.Slf4j;
import nqt.base_java_spring_be.dto.CustomResponse;
import nqt.base_java_spring_be.tts.dto.TextToMp3Request;
import nqt.base_java_spring_be.tts.dto.TextToMp3Result;
import nqt.base_java_spring_be.tts.service.iservices.TtsAIService;
import nqt.base_java_spring_be.tts.service.iservices.TtsService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Slf4j
@RestController
@RequestMapping("/api/tts")
@Validated
public class TtsAIController {

    private final TtsAIService tts;

    public TtsAIController(TtsAIService tts) {
        this.tts = tts;
    }

    @PostMapping("/vi/text-to-mp3")
    public ResponseEntity<CustomResponse<?>> textToMp3(@Valid @RequestBody TextToMp3Request req) {
        try {
            TextToMp3Result data = tts.textToMp3(req);

            CustomResponse<TextToMp3Result> responseBody = CustomResponse.success(data, "Chuyển văn bản thành MP3 thành công");
            return ResponseEntity.ok(responseBody);

        } catch (IllegalArgumentException e) {
            CustomResponse<?> responseBody = CustomResponse.error(e.getMessage(), HttpStatus.BAD_REQUEST);
            return ResponseEntity.badRequest().body(responseBody);

        } catch (Exception e) {

            CustomResponse<?> responseBody = CustomResponse.error("Lỗi hệ thống: " + e.getMessage(), HttpStatus.INTERNAL_SERVER_ERROR);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(responseBody);
        }
    }
}
