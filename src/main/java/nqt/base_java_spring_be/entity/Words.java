package nqt.base_java_spring_be.entity;

import jakarta.persistence.*;
import lombok.*;
import lombok.experimental.FieldDefaults;
import nqt.base_java_spring_be.enums.ProjectStatus;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

@Entity
@Table(name = "words")
@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@ToString(callSuper = true)
@FieldDefaults(level = AccessLevel.PRIVATE)
public class Words extends BaseEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.AUTO)
    UUID id;

    @Column(nullable = false, length = 255)
    String fullName;

    @Column(nullable = false, length = 255)
    String name;

    @Column(nullable = false, length = 10)
    String type;

    @Column(nullable = false, length = 255)
    String path;

    public Words(String fullName, String name, String type, String path) {
        this.fullName = fullName;
        this.name = name;
        this.type = type;
        this.path = path;
    }
}
