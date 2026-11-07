package com.xenova.backend.agent.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import java.time.Instant;
import java.util.Objects;

@Entity
@Table(name = "agent_command_results",
        uniqueConstraints = @UniqueConstraint(name = "uk_agent_command_results_vm_command",
                columnNames = {"vm_id", "command_id"}),
        indexes = @Index(name = "idx_agent_command_results_operation", columnList = "operation_id"))
public class AgentCommandResult {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "vm_id", nullable = false, length = 36)
    private String vmId;

    @Column(name = "command_id", nullable = false, length = 128)
    private String commandId;

    @Column(name = "operation_id", length = 128)
    private String operationId;

    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 12)
    private AgentCommandStatus status;

    @Column(name = "boot_id", nullable = false, length = 64)
    private String bootId;

    @Column(name = "agent_instance_id", nullable = false, length = 64)
    private String agentInstanceId;

    @Column(name = "message_id", nullable = false, length = 128)
    private String messageId;

    @Column(nullable = false)
    private long seq;

    @Column(name = "started_at")
    private Instant startedAt;

    @Column(name = "finished_at")
    private Instant finishedAt;

    @Column(name = "duration_ms")
    private Long durationMs;

    @Column(name = "result_json", columnDefinition = "longtext")
    private String resultJson;

    @Column(name = "error_json", columnDefinition = "longtext")
    private String errorJson;

    @Column(name = "received_at", nullable = false)
    private Instant receivedAt;

    @Column(name = "contract_violation", nullable = false)
    private boolean contractViolation;

    @Column(name = "violation_detail", length = 255)
    private String violationDetail;

    protected AgentCommandResult() {
    }

    public AgentCommandResult(String vmId, AgentCommandResultReport report) {
        this.vmId = vmId;
        this.commandId = report.commandId();
        apply(report);
    }

    public Observation observe(AgentCommandResultReport report) {
        if (sameResult(report)) {
            return Observation.DUPLICATE;
        }
        if (status == AgentCommandStatus.RUNNING
                && (report.status() == AgentCommandStatus.SUCCEEDED || report.status() == AgentCommandStatus.FAILED)
                && Objects.equals(operationId, report.operationId())) {
            apply(report);
            return Observation.UPDATED;
        }
        if (status.terminal() && report.status() == AgentCommandStatus.RUNNING) {
            return Observation.DUPLICATE;
        }
        contractViolation = true;
        violationDetail = "invalid transition " + status + " -> " + report.status();
        receivedAt = report.receivedAt();
        return Observation.CONTRACT_VIOLATION;
    }

    private boolean sameResult(AgentCommandResultReport report) {
        return status == report.status()
                && Objects.equals(operationId, report.operationId())
                && Objects.equals(startedAt, report.startedAt())
                && Objects.equals(finishedAt, report.finishedAt())
                && Objects.equals(durationMs, report.durationMs())
                && Objects.equals(resultJson, report.resultJson())
                && Objects.equals(errorJson, report.errorJson());
    }

    private void apply(AgentCommandResultReport report) {
        operationId = report.operationId();
        status = report.status();
        bootId = report.bootId();
        agentInstanceId = report.agentInstanceId();
        messageId = report.messageId();
        seq = report.seq();
        startedAt = report.startedAt();
        finishedAt = report.finishedAt();
        durationMs = report.durationMs();
        resultJson = report.resultJson();
        errorJson = report.errorJson();
        receivedAt = report.receivedAt();
    }

    public Long getId() {
        return id;
    }

    public String getVmId() {
        return vmId;
    }

    public String getCommandId() {
        return commandId;
    }

    public String getOperationId() {
        return operationId;
    }

    public AgentCommandStatus getStatus() {
        return status;
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

    public String getResultJson() {
        return resultJson;
    }

    public String getErrorJson() {
        return errorJson;
    }

    public Instant getReceivedAt() {
        return receivedAt;
    }

    public boolean isContractViolation() {
        return contractViolation;
    }

    public String getViolationDetail() {
        return violationDetail;
    }

    public enum Observation {
        UPDATED,
        DUPLICATE,
        CONTRACT_VIOLATION
    }
}
