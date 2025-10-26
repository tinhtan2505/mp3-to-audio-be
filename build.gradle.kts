plugins {
	java
	// Boot 3.5.7 kéo Spring Framework 6.2.12+ và Spring Security 6.5.4+
	id("org.springframework.boot") version "3.5.7"
	id("io.spring.dependency-management") version "1.1.7"
}

group = "nqt"
version = "0.0.1-SNAPSHOT"

java {
	toolchain { languageVersion = JavaLanguageVersion.of(21) }
}

repositories { mavenCentral() }

// Giữ nếu bạn cần ép Netty riêng
extra["netty.version"] = "4.1.125.Final"

dependencies {
	// Spring Boot Starters (đồng bộ theo Boot 3.5.7)
	implementation("org.springframework.boot:spring-boot-starter:3.5.7")
	implementation("org.springframework.boot:spring-boot-starter-web:3.5.7")
	implementation("org.springframework.boot:spring-boot-starter-data-jpa:3.5.7")
	implementation("org.springframework.boot:spring-boot-starter-security:3.5.7")
	implementation("org.springframework.boot:spring-boot-starter-validation:3.5.7")
	implementation("org.springframework.boot:spring-boot-starter-websocket:3.5.7")
	implementation("org.springframework.boot:spring-boot-starter-actuator:3.5.7")
	implementation("org.springframework.boot:spring-boot-starter-reactor-netty:3.5.7")

	// Ép Framework modules lên bản đã vá STOMP (6.2.12)
	implementation("org.springframework:spring-messaging:6.2.12")
	implementation("org.springframework:spring-websocket:6.2.12")

	// Database
	runtimeOnly("org.postgresql:postgresql:42.7.7")

	// JWT
	implementation("io.jsonwebtoken:jjwt-api:0.12.6")
	runtimeOnly("io.jsonwebtoken:jjwt-impl:0.12.6")
	runtimeOnly("io.jsonwebtoken:jjwt-jackson:0.12.6")

	// Swagger / OpenAPI
	implementation("org.apache.commons:commons-lang3:3.18.0")
	implementation("org.springdoc:springdoc-openapi-starter-webmvc-ui:2.8.9")

	// Lombok
	compileOnly("org.projectlombok:lombok:1.18.38")
	annotationProcessor("org.projectlombok:lombok:1.18.38")

	// Test
	testImplementation("org.springframework.boot:spring-boot-starter-test:3.5.7")
	testRuntimeOnly("org.junit.platform:junit-platform-launcher:1.13.4")
}

tasks.withType<Test> { useJUnitPlatform() }
