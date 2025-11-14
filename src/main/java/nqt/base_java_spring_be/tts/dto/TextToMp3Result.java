package nqt.base_java_spring_be.tts.dto;

import lombok.*;

import java.util.List;

@Data
@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class TextToMp3Result {
    private byte[] audio;
    private List<String> notFoundWords;
}
