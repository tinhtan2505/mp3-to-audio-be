package nqt.base_java_spring_be.tts.service.iservices;

import nqt.base_java_spring_be.tts.dto.DubbingFileRequest;
import nqt.base_java_spring_be.tts.dto.DubbingResult;

public interface DubbingService {
    DubbingResult dubbingFromWhisper(DubbingFileRequest req);
}