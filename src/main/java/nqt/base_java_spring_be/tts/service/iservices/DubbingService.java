package nqt.base_java_spring_be.tts.service.iservices;

import nqt.base_java_spring_be.tts.dto.DetectTextRequest;
import nqt.base_java_spring_be.tts.dto.DubbingFileRequest;
import nqt.base_java_spring_be.tts.dto.DubbingResult;
import nqt.base_java_spring_be.tts.dto.MixVideoRequest;

public interface DubbingService {
    DubbingResult dubbingFromWhisper(DubbingFileRequest req);
    DubbingResult generateDubbingAudio(DubbingFileRequest req);
    DubbingResult mixVideo(MixVideoRequest req);
    DubbingResult translateSrt(DubbingFileRequest req);
    Object detectTextRegions(DetectTextRequest req);
}