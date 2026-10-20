package com.xenova.backend.report.performance.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "performance_trials")
public class PerformanceTrial {

    @Id
    @Column(name = "trial_id", length = 36)
    private String trialId;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 32)
    private PerformanceMetric metric;

    @Column(name = "definition_id", nullable = false, length = 64)
    private String definitionId;

    @Column(name = "vm_id", length = 36)
    private String vmId;

    @Column(name = "source_vm_id", length = 36)
    private String sourceVmId;

    @Column(name = "target_vm_id", length = 36)
    private String targetVmId;

    @Column(name = "build_id", length = 64)
    private String buildId;

    @Column(name = "template_version", length = 64)
    private String templateVersion;

    @Column(name = "operation_id", length = 64)
    private String operationId;

    @Column(name = "started_at", nullable = false)
    private Instant startedAt;

    @Column(name = "finished_at")
    private Instant finishedAt;

    @Column(name = "duration_ms")
    private Long durationMs;

    @Column(nullable = false)
    private boolean success;

    @Column(name = "failure_reason", length = 255)
    private String failureReason;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 16)
    private MeasurementEnvironment environment;

    @Column(name = "raw_log_ref", length = 255)
    private String rawLogRef;

    protected PerformanceTrial() {
    }

    public PerformanceTrial(String trialId, NewPerformanceTrial trial) {
        this.trialId = trialId;
        this.metric = trial.metric();
        this.definitionId = trial.definitionId();
        this.vmId = trial.vmId();
        this.sourceVmId = trial.sourceVmId();
        this.targetVmId = trial.targetVmId();
        this.buildId = trial.buildId();
        this.templateVersion = trial.templateVersion();
        this.operationId = trial.operationId();
        this.startedAt = trial.startedAt();
        this.finishedAt = trial.finishedAt();
        this.durationMs = trial.durationMs();
        this.success = trial.success();
        this.failureReason = trial.failureReason();
        this.environment = trial.environment();
        this.rawLogRef = trial.rawLogRef();
    }

    public String getTrialId() {
        return trialId;
    }

    public PerformanceMetric getMetric() {
        return metric;
    }

    public String getDefinitionId() {
        return definitionId;
    }

    public String getVmId() {
        return vmId;
    }

    public String getSourceVmId() {
        return sourceVmId;
    }

    public String getTargetVmId() {
        return targetVmId;
    }

    public String getBuildId() {
        return buildId;
    }

    public String getTemplateVersion() {
        return templateVersion;
    }

    public String getOperationId() {
        return operationId;
    }

    public Instant getStartedAt() {
        return startedAt;
    }

    public Instant getFinishedAt() {
        return finishedAt;
    }

    public Long getDurationMs() {
        return durationMs;
    }

    public boolean isSuccess() {
        return success;
    }

    public String getFailureReason() {
        return failureReason;
    }

    public MeasurementEnvironment getEnvironment() {
        return environment;
    }

    public String getRawLogRef() {
        return rawLogRef;
    }
}
