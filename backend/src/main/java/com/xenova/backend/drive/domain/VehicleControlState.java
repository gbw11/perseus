package com.xenova.backend.drive.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "vehicle_control_states", indexes =
        @Index(name = "idx_vehicle_control_states_active_vm_id", columnList = "active_vm_id"))
public class VehicleControlState {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "scope_key", nullable = false, unique = true, length = 50)
    private String scopeKey;

    @Column(name = "desired_mode", length = 30)
    private String desiredMode;

    @Column(name = "active_mode", length = 30)
    private String activeMode;

    @Column(name = "active_vm_id", length = 36)
    private String activeVmId;

    @Column(name = "active_app_id", length = 100)
    private String activeAppId;

    private Long generation;

    @Column(name = "applied_generation")
    private Long appliedGeneration;

    @Column(name = "control_boot_id", length = 64)
    private String controlBootId;

    @Column(name = "control_instance_id", length = 36)
    private String controlInstanceId;

    @Column(name = "control_seq")
    private Long controlSeq;

    @Column(name = "control_received_at")
    private Instant controlReceivedAt;

    @Column(name = "status_boot_id", length = 64)
    private String statusBootId;

    @Column(name = "status_instance_id", length = 36)
    private String statusInstanceId;

    @Column(name = "status_seq")
    private Long statusSeq;

    @Column(name = "status_received_at")
    private Instant statusReceivedAt;

    @Column(name = "mode_changed_at")
    private Instant modeChangedAt;

    @Column(name = "updated_at", nullable = false)
    private Instant updatedAt;

    protected VehicleControlState() {
    }

    public VehicleControlState(String scopeKey, Instant updatedAt) {
        this.scopeKey = scopeKey;
        this.updatedAt = updatedAt;
    }

    public void updateControl(
            String desiredMode,
            String activeMode,
            String activeVmId,
            String activeAppId,
            Long generation,
            String bootId,
            String instanceId,
            Long seq,
            Instant receivedAt,
            Instant modeChangedAt) {
        this.desiredMode = desiredMode;
        this.activeMode = activeMode;
        this.activeVmId = activeVmId;
        this.activeAppId = activeAppId;
        this.generation = generation;
        this.controlBootId = bootId;
        this.controlInstanceId = instanceId;
        this.controlSeq = seq;
        this.controlReceivedAt = receivedAt;
        this.modeChangedAt = modeChangedAt;
        this.updatedAt = receivedAt;
    }

    public void updateVehicleStatus(
            Long appliedGeneration, String bootId, String instanceId, Long seq, Instant receivedAt) {
        this.appliedGeneration = appliedGeneration;
        this.statusBootId = bootId;
        this.statusInstanceId = instanceId;
        this.statusSeq = seq;
        this.statusReceivedAt = receivedAt;
        this.updatedAt = receivedAt;
    }

    public Long getId() { return id; }
    public String getScopeKey() { return scopeKey; }
    public String getDesiredMode() { return desiredMode; }
    public String getActiveMode() { return activeMode; }
    public String getActiveVmId() { return activeVmId; }
    public String getActiveAppId() { return activeAppId; }
    public Long getGeneration() { return generation; }
    public Long getAppliedGeneration() { return appliedGeneration; }
    public String getControlBootId() { return controlBootId; }
    public String getControlInstanceId() { return controlInstanceId; }
    public Long getControlSeq() { return controlSeq; }
    public Instant getControlReceivedAt() { return controlReceivedAt; }
    public String getStatusBootId() { return statusBootId; }
    public String getStatusInstanceId() { return statusInstanceId; }
    public Long getStatusSeq() { return statusSeq; }
    public Instant getStatusReceivedAt() { return statusReceivedAt; }
    public Instant getModeChangedAt() { return modeChangedAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
