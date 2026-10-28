package com.xenova.backend.event.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "vm_recovery_health", indexes =
        @Index(name = "idx_vm_recovery_health_status", columnList = "status"))
public class VmRecoveryHealth {

    @Id
    @Column(name = "vm_id", length = 36)
    private String vmId;

    @Column(name = "last_event_id", unique = true, length = 36)
    private String lastEventId;

    @Column(name = "recovery_id", length = 36)
    private String recoveryId;

    @Column(name = "recovery_attempt_id", length = 36)
    private String recoveryAttemptId;

    @Column(nullable = false, length = 20)
    private String status;

    private Integer attempt;

    @Column(name = "recoveries_total", nullable = false)
    private long recoveriesTotal;

    @Column(name = "final_failures_total", nullable = false)
    private long finalFailuresTotal;

    @Column(name = "last_recovery_duration_ms")
    private Long lastRecoveryDurationMs;

    @Column(name = "last_occurred_at")
    private Instant lastOccurredAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected VmRecoveryHealth() {
    }

    public VmRecoveryHealth(String vmId, Instant updatedAt) {
        this.vmId = vmId;
        this.status = "UNKNOWN";
        this.updatedAt = updatedAt;
    }

    public void update(
            String eventId,
            String recoveryId,
            String recoveryAttemptId,
            String status,
            Integer attempt,
            boolean completed,
            boolean finalFailure,
            Long completedRecoveryDurationMs,
            Instant occurredAt,
            Instant updatedAt) {
        if (completed) {
            this.recoveriesTotal++;
            this.lastRecoveryDurationMs = completedRecoveryDurationMs;
        }
        if (finalFailure) {
            this.finalFailuresTotal++;
        }
        if (this.lastOccurredAt == null || !occurredAt.isBefore(this.lastOccurredAt)) {
            this.lastEventId = eventId;
            this.recoveryId = recoveryId;
            this.recoveryAttemptId = recoveryAttemptId;
            this.status = status;
            this.attempt = attempt;
            this.lastOccurredAt = occurredAt;
        }
        this.updatedAt = updatedAt;
    }

    public String getVmId() { return vmId; }
    public String getLastEventId() { return lastEventId; }
    public String getRecoveryId() { return recoveryId; }
    public String getRecoveryAttemptId() { return recoveryAttemptId; }
    public String getStatus() { return status; }
    public Integer getAttempt() { return attempt; }
    public long getRecoveriesTotal() { return recoveriesTotal; }
    public long getFinalFailuresTotal() { return finalFailuresTotal; }
    public Long getLastRecoveryDurationMs() { return lastRecoveryDurationMs; }
    public Instant getLastOccurredAt() { return lastOccurredAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
