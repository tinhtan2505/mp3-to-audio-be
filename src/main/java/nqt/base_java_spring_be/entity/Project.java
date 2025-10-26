package nqt.base_java_spring_be.entity;

import jakarta.persistence.*;
import lombok.*;
import lombok.experimental.FieldDefaults;
import nqt.base_java_spring_be.enums.ProjectStatus;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

@Entity
@Table(name = "project")
@Getter
@Setter
@Builder
@NoArgsConstructor
@AllArgsConstructor
@ToString(callSuper = true)
@FieldDefaults(level = AccessLevel.PRIVATE)
public class Project extends BaseEntity {

    @Id
    @GeneratedValue(strategy = GenerationType.AUTO)
    UUID id;

    @Column(nullable = false, unique = true, length = 32)
    String code;

    @Column(nullable = false, length = 255)
    String name;

    @Column(length = 255)
    String owner;

    @Enumerated(EnumType.ORDINAL)
    @Column(nullable = false)
    ProjectStatus status;

    @Column(name = "start_date")
    LocalDate startDate;

    @Column(name = "due_date")
    LocalDate dueDate;

    @Column(precision = 18, scale = 2)
    BigDecimal budget;

    Integer progress; // 0-100

    @ElementCollection
    @CollectionTable(name = "project_tags", joinColumns = @JoinColumn(name = "project_id"))
    @Column(name = "tag")
    List<String> tags;

    @Column(columnDefinition = "TEXT")
    String description;
}
