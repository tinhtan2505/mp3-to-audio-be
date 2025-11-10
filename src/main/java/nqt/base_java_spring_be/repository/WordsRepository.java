package nqt.base_java_spring_be.repository;

import nqt.base_java_spring_be.entity.Words;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;
import java.util.UUID;

@Repository
public interface WordsRepository extends JpaRepository<Words, UUID> {
    Optional<Words> findFirstByNameIgnoreCase(String name);
}
