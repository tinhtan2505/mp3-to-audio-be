package nqt.base_java_spring_be.tts.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@Builder
@AllArgsConstructor
@NoArgsConstructor
public class ViettelTtsRequest {
    private String text;

    private String voice; // Ví dụ: hn-quynhanh, hcm-diemmy

    private float speed; // 0.8 đến 1.2

    @JsonProperty("tts_return_option")
    private int ttsReturnOption; // 2: wav, 3: mp3

    private String token; // Token Viettel AI của bạn

    @JsonProperty("without_filter")
    private boolean withoutFilter;
}
