package nqt.base_java_spring_be.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import nqt.base_java_spring_be.entity.User;

import java.util.Optional;
import java.util.UUID;

public interface UserRepository extends JpaRepository<User, UUID> {
    Optional<User> findByUsername(String username);

    boolean existsByUsername(String username);

    boolean existsByEmail(String email);
}

