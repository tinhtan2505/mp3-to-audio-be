package nqt.base_java_spring_be.config;

import lombok.RequiredArgsConstructor;
import nqt.base_java_spring_be.authentication.dto.UserPrincipal;
import nqt.base_java_spring_be.entity.User;
import nqt.base_java_spring_be.repository.UserRepository;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.domain.AuditorAware;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.core.userdetails.UserDetails;

import java.util.Optional;
import java.util.UUID;

@Configuration
@EnableJpaAuditing // rất quan trọng!
@RequiredArgsConstructor
public class AuditingConfig {

    private final UserRepository userRepository;

    @Bean
    public AuditorAware<UUID> auditorAware() {
        return () -> {
            Authentication auth = SecurityContextHolder.getContext().getAuthentication();
            if (auth == null || !auth.isAuthenticated()) return Optional.empty();

            Object principal = auth.getPrincipal();

            // 1) Nếu bạn dùng principal tuỳ biến có getId()
            if (principal instanceof UserPrincipal up) {      // class ở bên dưới
                return Optional.ofNullable(up.getId());
            }

            // 2) Nếu bạn đặt luôn entity User làm principal
            if (principal instanceof User u) {
                return Optional.ofNullable(u.getId());
            }

            // 3) Nếu chỉ có username (UserDetails mặc định), tra DB để lấy UUID
            if (principal instanceof UserDetails ud) {
                return userRepository.findByUsername(ud.getUsername())
                        .map(User::getId);
            }

            return Optional.empty();
        };
    }
}
