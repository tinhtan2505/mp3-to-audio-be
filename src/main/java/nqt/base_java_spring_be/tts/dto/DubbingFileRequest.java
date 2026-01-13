package nqt.base_java_spring_be.tts.dto;

import jakarta.validation.constraints.NotBlank;
import lombok.Data;

@Data
public class DubbingFileRequest {
    @NotBlank(message = "Đường dẫn file không được để trống")
    private String inputPath;

    private Boolean enableDiarization;
}
