package nqt.base_java_spring_be.tts.dto;

import lombok.Data;

@Data
public class MixVideoRequest {
    private String videoInput;
    private String instrumental;
    private String voiceDub;
    private Double musicVolume;
    private Double voiceVolume;
    private Double duckingRatio;
    private Integer attackTime;
    private Integer releaseTime;
    private Boolean removeLogo;
    private Integer logoX;
    private Integer logoY;
    private Integer logoW;
    private Integer logoH;
    private boolean crop = false;
    private String subtitlePath;
    private Integer subtitleFontSize;
    private Integer subtitleBorderWidth;
    private Boolean watermarkLines;
}
