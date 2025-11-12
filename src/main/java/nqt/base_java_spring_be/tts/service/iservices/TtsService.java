package nqt.base_java_spring_be.tts.service.iservices;

import nqt.base_java_spring_be.tts.dto.TextToMp3Request;

public interface TtsService {
    byte[] synthesizeOneWordVi(String word);
    void insertWords();
    void textToMp3(TextToMp3Request req);
}