package nqt.base_java_spring_be;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.data.jpa.repository.config.EnableJpaAuditing;

@SpringBootApplication
public class BaseJavaSpringBeApplication {

	public static void main(String[] args) {
		SpringApplication.run(BaseJavaSpringBeApplication.class, args);
	}

}
