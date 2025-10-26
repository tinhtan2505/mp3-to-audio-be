package nqt.base_java_spring_be.enums;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;
import lombok.Getter;

import java.util.Collections;
import java.util.EnumSet;
import java.util.Map;
import java.util.Objects;
import java.util.function.Function;
import java.util.stream.Collectors;

@Getter
public enum ProjectStatus {
    PLANNING(0, "Planning"),
    ACTIVE(1, "Active"),
    PAUSED(2, "Paused"),
    DONE(3, "Done");

    private final int code;
    private final String label;

    ProjectStatus(int code, String label) {
        this.code = code;
        this.label = label;
    }

    // ---- Tra cứu an toàn & bất biến ----
    private static final Map<Integer, ProjectStatus> BY_CODE;
    static {
        Map<Integer, ProjectStatus> m =
                EnumSet.allOf(ProjectStatus.class).stream()
                        .collect(Collectors.toMap(ProjectStatus::getCode, Function.identity(), (a,b) -> {
                            // Nếu có trùng code -> fail fast để không deploy cấu hình lỗi
                            throw new IllegalStateException("Duplicate ProjectStatus code: " + a.code);
                        }));
        BY_CODE = Collections.unmodifiableMap(m);
    }

    // ---- JSON ra: chỉ trả code int ----
    @JsonValue
    public int json() {
        return code;
    }

    // ---- JSON vào: nhận int và validate ----
    @JsonCreator(mode = JsonCreator.Mode.DELEGATING)
    public static ProjectStatus fromJson(Integer code) {
        if (code == null) throw new IllegalArgumentException("ProjectStatus code is null");
        ProjectStatus s = BY_CODE.get(code);
        if (s == null) throw new IllegalArgumentException("Unknown ProjectStatus code: " + code);
        return s;
    }

    // ---- API nội bộ: nhận int (ví dụ từ DB) ----
    public static ProjectStatus fromCode(int code) {
        ProjectStatus s = BY_CODE.get(code);
        if (s == null) throw new IllegalArgumentException("Unknown ProjectStatus code: " + code);
        return s;
    }

    // ---- (Tùy chọn) Quy tắc chuyển trạng thái hợp lệ ----
    public boolean canTransitionTo(ProjectStatus next) {
        Objects.requireNonNull(next, "next status");
        return switch (this) {
            case PLANNING -> next == ACTIVE || next == PAUSED;
            case ACTIVE   -> next == PAUSED || next == DONE;
            case PAUSED   -> next == ACTIVE || next == DONE;
            case DONE     -> false; // terminal
        };
    }

    public boolean isTerminal() {
        return this == DONE;
    }
}
