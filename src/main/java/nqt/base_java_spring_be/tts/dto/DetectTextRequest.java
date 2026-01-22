package nqt.base_java_spring_be.tts.dto;

import lombok.Data;
import jakarta.validation.constraints.NotBlank;

@Data
public class DetectTextRequest {
    @NotBlank(message = "Đường dẫn video không được để trống")
    private String videoPath;

    // Mặc định là true nếu null
    private Boolean skipTopTwoThirds = true;
}
