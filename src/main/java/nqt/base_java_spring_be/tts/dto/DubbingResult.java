package nqt.base_java_spring_be.tts.dto;

import lombok.*;

import java.util.List;

@Data
@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class DubbingResult {
    private String status;
    private String outputFilePath;}
