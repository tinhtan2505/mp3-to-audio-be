package nqt.base_java_spring_be.entity;

import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;
import lombok.ToString;
import org.springframework.data.annotation.CreatedBy;
import org.springframework.data.annotation.CreatedDate;
import org.springframework.data.annotation.LastModifiedBy;
import org.springframework.data.annotation.LastModifiedDate;
import org.springframework.data.jpa.domain.support.AuditingEntityListener;

import java.io.Serializable;
import java.time.Instant;
import java.util.UUID;

@MappedSuperclass
@EntityListeners(AuditingEntityListener.class)
@Getter @Setter @ToString
public abstract class BaseEntity implements Serializable {

    @CreatedDate
    @Column(name = "ngay_tao", nullable = false, updatable = false)
    private Instant createdAt;

    @LastModifiedDate
    @Column(name = "ngay_sua")
    private Instant updatedAt;

    @Column(name = "ngay_xoa")
    private Instant deletedAt;

    @Column(name = "trang_thai", nullable = false)
    private boolean active = true;

    @CreatedBy
    @Column(name = "nguoi_tao", updatable = false)
    private UUID createdBy;

    @LastModifiedBy
    @Column(name = "nguoi_sua")
    private UUID updatedBy;

    /** Soft delete: đánh dấu đã xóa và vô hiệu hóa. */
    public void markDeleted() {
        this.deletedAt = Instant.now();
        this.active = false;
    }

    public boolean isDeleted() {
        return this.deletedAt != null;
    }
}
