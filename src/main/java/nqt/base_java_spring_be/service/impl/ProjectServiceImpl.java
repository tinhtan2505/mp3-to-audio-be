package nqt.base_java_spring_be.service.impl;

import lombok.RequiredArgsConstructor;
import nqt.base_java_spring_be.entity.Project;
import nqt.base_java_spring_be.exception.BadRequestException;
import nqt.base_java_spring_be.exception.ResourceNotFoundException;
import nqt.base_java_spring_be.realtime.RealtimeEvents;
import nqt.base_java_spring_be.repository.ProjectRepository;
import nqt.base_java_spring_be.service.iservices.ProjectService;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.UUID;

@Service
@RequiredArgsConstructor
@Transactional
public class ProjectServiceImpl  implements ProjectService {
    private final ProjectRepository repo;
    private final RealtimeEvents realtime;

    @Override
    public Project create(Project project) {
        String code = project.getCode() == null ? null : project.getCode().trim();
        if (code == null || code.isEmpty()) {
            throw new BadRequestException("Mã dự án không được để trống");
        }
        if (repo.existsByCode(project.getCode())) {
            throw new BadRequestException("Mã dự án đã tồn tại: " + project.getCode());
        }
        var saved = repo.save(project);

        // 🔔 phát realtime (sẽ gửi sau COMMIT nhờ TransactionalEventListener)
        realtime.emitCreated("Project", saved.getId().toString(), saved, getActorUsername());
        return saved;
    }

    @Override
    @Transactional(readOnly = true)
    public List<Project> findAll() {
        return repo.findByActiveTrue();
    }

    @Override
    @Transactional(readOnly = true)
    public Project findById(UUID id) {
        return repo.findById(id)
                .filter(Project::isActive) // ẩn record đã soft-delete
                .orElseThrow(() -> new ResourceNotFoundException("Project not found: " + id));
    }

    @Override
    public Project update(UUID id, Project update) {
        // Lấy bản ghi hiện tại (đã ẩn soft-delete trong findById)
        Project current = findById(id);

        // ---- Validate code (nếu đổi code thì phải unique) ----
        String newCode = update.getCode() == null ? null : update.getCode().trim();
        if (newCode == null || newCode.isEmpty()) {
            throw new BadRequestException("Mã dự án không được để trống");
        }
        // Nếu code thay đổi, kiểm tra trùng
        if (!newCode.equalsIgnoreCase(current.getCode())) {
            boolean exists = repo.existsByCodeIgnoreCaseAndIdNot(newCode, id);
            if (exists) {
                throw new BadRequestException("Mã dự án đã tồn tại: " + newCode);
            }
            current.setCode(newCode);
        }

        // ---- Gán các trường thuộc Project (KHÔNG chạm BaseEntity) ----
        current.setName(update.getName());
        current.setOwner(update.getOwner());
        current.setStatus(update.getStatus());
        current.setStartDate(update.getStartDate());
        current.setDueDate(update.getDueDate());
        current.setBudget(update.getBudget());
        current.setProgress(update.getProgress());
        // Với @ElementCollection, set trực tiếp list mới (ghi đè toàn bộ)
        current.setTags(update.getTags());
        current.setDescription(update.getDescription());

        var saved = repo.save(current);

        // 🔔 phát realtime
        realtime.emitUpdated("Project", saved.getId().toString(), saved, getActorUsername());
        return saved;
    }


    @Override
    public void delete(UUID id) {
        Project current = findById(id);
        current.markDeleted();
        var saved = repo.save(current);

        realtime.emitDeleted("Project", saved.getId().toString(), getActorUsername());
    }

    private String getActorUsername() {
        Authentication auth = SecurityContextHolder.getContext().getAuthentication();
        if (auth == null) return "system";
        Object principal = auth.getPrincipal();
        if (principal instanceof nqt.base_java_spring_be.authentication.dto.UserPrincipal up) return up.getUsername();
        if (principal instanceof org.springframework.security.core.userdetails.UserDetails ud) return ud.getUsername();
        return auth.getName();
    }
}

