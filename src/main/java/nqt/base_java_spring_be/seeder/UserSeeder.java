package nqt.base_java_spring_be.seeder;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.CommandLineRunner;
import org.springframework.core.annotation.Order;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;
import nqt.base_java_spring_be.entity.User;
import nqt.base_java_spring_be.repository.UserRepository;

import java.time.LocalDate;
import java.util.Set;

@Component
@Order(1)
public class UserSeeder implements CommandLineRunner {

    @Value("${seeder.enabled}")
    private boolean isSeederEnabled;

    @Autowired
    private UserRepository userRepository;

    @Autowired
    private PasswordEncoder passwordEncoder;

    @Override
    public void run(String... args) throws Exception {
        if (!isSeederEnabled) {
            return;
        }

        if (userRepository.count() == 0) {
            User user1 = new User();
            user1.setUsername("admin");
            user1.setPassword(passwordEncoder.encode("123123123"));
            user1.setFirstName("Admin");
            user1.setLastName("User");
            user1.setDob(LocalDate.of(1997, 4, 19));
            user1.setEmail("admin@example.com");
            user1.setRoles(Set.of("ROLE_ADMIN", "ROLE_USER"));

            User user2 = new User();
            user2.setUsername("user1");
            user2.setPassword(passwordEncoder.encode("user123"));
            user2.setFirstName("Regular");
            user2.setLastName("User");
            user2.setDob(LocalDate.of(1995, 5, 15));
            user2.setEmail("user@example.com");
            user2.setRoles(Set.of("ROLE_USER"));

            userRepository.save(user1);
            userRepository.save(user2);

            System.out.println("Seeded data for User entity.");
        }
    }
}
