package nqt.base_java_spring_be.tts.service.iservices;

import nqt.base_java_spring_be.tts.dto.TextToMp3Request;
import nqt.base_java_spring_be.tts.dto.TextToMp3Result;

public interface TtsService {
    void insertWords();
    TextToMp3Result textToMp3(TextToMp3Request req);
}