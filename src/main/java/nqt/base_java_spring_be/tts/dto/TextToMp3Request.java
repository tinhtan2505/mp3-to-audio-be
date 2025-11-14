package nqt.base_java_spring_be.tts.dto;

import lombok.Data;

@Data
public class TextToMp3Request {
    private String word;
    private PauseConfig pauses;

    @Data
    public static class PauseConfig {
        private double wordPause;        // giữa hai từ
        private double dotPause;         // .
        private double commaPause;       // ,
        private double semicolonPause;   // ;
        private double colonPause;       // :
        private double questionPause;    // ?
        private double exclamationPause; // !
        private double lineBreakPause;   // xuống dòng
        private double parenthesisPause; // () hoặc ""
    }
}
