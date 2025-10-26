package nqt.base_java_spring_be.dto.request;

import jakarta.validation.constraints.*;
import lombok.Data;
import nqt.base_java_spring_be.enums.ProjectStatus;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

@Data
public class ProjectCreateRequest {
    @NotBlank
    @Size(max = 32)
    private String code;

    @NotBlank
    @Size(max = 255)
    private String name;

    @Size(max = 255)
    private String owner;

    @NotNull
    private ProjectStatus status;

    private LocalDate startDate;
    private LocalDate dueDate;

    @Digits(integer = 16, fraction = 2)
    @PositiveOrZero
    private BigDecimal budget;

    @Min(0) @Max(100)
    private Integer progress;

    private List<String> tags;

    private String description;
}
