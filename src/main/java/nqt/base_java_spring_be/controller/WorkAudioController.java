package nqt.base_java_spring_be.controller;

import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import nqt.base_java_spring_be.dto.CustomResponse;
import nqt.base_java_spring_be.dto.request.ProjectCreateRequest;
import nqt.base_java_spring_be.entity.Project;
import nqt.base_java_spring_be.service.iservices.ProjectService;
import nqt.base_java_spring_be.service.iservices.WorkAudioService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.net.URI;
import java.util.UUID;

@RestController
@CrossOrigin
@RequestMapping("api/work-audio")
@Tag(name = "Work Audio")
@RequiredArgsConstructor
public class WorkAudioController {
    private final WorkAudioService service;

    @PostMapping("/build")
    public ResponseEntity<CustomResponse<Project>> build(@Valid @RequestBody ProjectCreateRequest req) {
        service.build();
        return ResponseEntity.ok(CustomResponse.success(null, "Tạo mới thành công"));
    }
}
