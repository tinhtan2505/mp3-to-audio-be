package nqt.base_java_spring_be.controller;

import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import nqt.base_java_spring_be.dto.CustomResponse;
import nqt.base_java_spring_be.dto.request.ProjectCreateRequest;
import nqt.base_java_spring_be.entity.Project;
import nqt.base_java_spring_be.service.iservices.ProjectService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.net.URI;
import java.util.UUID;

@RestController
@CrossOrigin
@RequestMapping("api/project")
@Tag(name = "Project")
@RequiredArgsConstructor
public class ProjectController {
    private final ProjectService service;

    @GetMapping("find-all")
    public ResponseEntity<?> getOptions() {
        var data = service.findAll();
        return ResponseEntity.ok(CustomResponse.success(data, "Thành công"));
    }

    @GetMapping("{id}")
    public ResponseEntity<?> findById(@PathVariable UUID id) {
        Project data = service.findById(id);
        return ResponseEntity.ok(CustomResponse.success(data, "Thành công"));
    }

    @PostMapping
    public ResponseEntity<CustomResponse<Project>> create(@Valid @RequestBody ProjectCreateRequest req) {
        var entity = Project.builder()
                .code(req.getCode())
                .name(req.getName())
                .owner(req.getOwner())
                .status(req.getStatus())
                .startDate(req.getStartDate())
                .dueDate(req.getDueDate())
                .budget(req.getBudget())
                .progress(req.getProgress())
                .tags(req.getTags())
                .description(req.getDescription())
                .build();

        var data = service.create(entity);
        var location = URI.create("/api/project/" + data.getId());
        return ResponseEntity.created(location)
                .body(CustomResponse.success(data, "Tạo mới thành công"));
    }

    @PutMapping("{id}")
    public ResponseEntity<CustomResponse<Project>> update(
            @PathVariable UUID id,
            @Valid @RequestBody ProjectCreateRequest req
    ) {
        // map từ request sang entity update
        var updateEntity = Project.builder()
                .code(req.getCode())
                .name(req.getName())
                .owner(req.getOwner())
                .status(req.getStatus())
                .startDate(req.getStartDate())
                .dueDate(req.getDueDate())
                .budget(req.getBudget())
                .progress(req.getProgress())
                .tags(req.getTags())
                .description(req.getDescription())
                .build();

        var data = service.update(id, updateEntity);
        return ResponseEntity.ok(CustomResponse.success(data, "Cập nhật thành công"));
    }

    @DeleteMapping("{id}")
    public ResponseEntity<CustomResponse<Void>> delete(@PathVariable UUID id) {
        service.delete(id);
        return ResponseEntity.ok(CustomResponse.success(null, "Xóa thành công"));
    }

}
