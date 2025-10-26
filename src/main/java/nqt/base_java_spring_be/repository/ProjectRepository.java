package nqt.base_java_spring_be.repository;

import nqt.base_java_spring_be.entity.Project;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface ProjectRepository extends JpaRepository<Project, UUID> {
    List<Project> findByActiveTrue();
    boolean existsByCode(String code);
    boolean existsByCodeIgnoreCaseAndIdNot(String code, UUID id);
}
