package nqt.base_java_spring_be.tts.dto;

import lombok.Data;

@Data
public class MixVideoRequest {
    private String videoInput;   // Đường dẫn video gốc
    private String instrumental; // Đường dẫn nhạc nền
    private String voiceDub;     // Đường dẫn giọng đọc
}
