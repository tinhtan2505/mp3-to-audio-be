package nqt.base_java_spring_be.tts.controller;

import jakarta.validation.Valid;
import lombok.extern.slf4j.Slf4j;
import nqt.base_java_spring_be.dto.CustomResponse;
import nqt.base_java_spring_be.tts.dto.DubbingFileRequest;
import nqt.base_java_spring_be.tts.dto.DubbingResult;
import nqt.base_java_spring_be.tts.service.iservices.DubbingService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@Slf4j
@RestController
@RequestMapping("/api/dubbing")
@Validated
public class DubbingController {

    private final DubbingService dubbingService;

    public DubbingController(DubbingService dubbingService) {
        this.dubbingService = dubbingService;
    }

    @PostMapping("/vi/dubbing-whisper")
    public ResponseEntity<CustomResponse<?>> dubbingFromWhisper(@Valid @RequestBody DubbingFileRequest req) {
        try {
            // Gọi service xử lý
            DubbingResult data = dubbingService.dubbingFromWhisper(req);

            CustomResponse<DubbingResult> responseBody = CustomResponse.success(data, "Xử lý lồng tiếng từ file thành công");
            return ResponseEntity.ok(responseBody);

        } catch (Exception e) {
            CustomResponse<?> responseBody = CustomResponse.error("Lỗi xử lý file: " + e.getMessage(), HttpStatus.INTERNAL_SERVER_ERROR);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(responseBody);
        }
    }

    @PostMapping("/vi/generate-dubbing-audio")
    public ResponseEntity<CustomResponse<?>> generateDubbingAudio(@Valid @RequestBody DubbingFileRequest req) {
        try {
            // req.getInputPath() ví dụ: "D:\\Dubbing\\pmh_vi.srt"
            DubbingResult data = dubbingService.generateDubbingAudio(req);

            CustomResponse<DubbingResult> responseBody = CustomResponse.success(data, "Tạo file âm thanh lồng tiếng thành công");
            return ResponseEntity.ok(responseBody);

        } catch (Exception e) {
            CustomResponse<?> responseBody = CustomResponse.error("Lỗi tạo audio: " + e.getMessage(), HttpStatus.INTERNAL_SERVER_ERROR);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body(responseBody);
        }
    }
}
