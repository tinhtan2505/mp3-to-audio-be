package nqt.base_java_spring_be.service.iservices;

import nqt.base_java_spring_be.entity.Project;

import java.util.List;
import java.util.UUID;

public interface ProjectService {
    Project create(Project project);
    List<Project> findAll();
    Project findById(UUID id);
    Project update(UUID id, Project update);
    void delete(UUID id);
}
