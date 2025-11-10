package nqt.base_java_spring_be.tts.dto;

import jakarta.validation.constraints.*;
import lombok.Data;
import nqt.base_java_spring_be.enums.ProjectStatus;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

@Data
public class TextToMp3Request {
    private String word;
}
