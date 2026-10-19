package com.xenova.backend.event.domain;

import com.xenova.backend.agent.domain.AgentStatus;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

// Last agent status announced as an event, so a restart does not repeat old transitions.
// Kept apart from agent_heartbeats, which the MQTT thread rewrites on every heartbeat.
@Entity
@Table(name = "reported_agent_statuses")
public class ReportedAgentStatus {

    @Id
    @Column(name = "vm_id", length = 36)
    private String vmId;

    @Enumerated(EnumType.STRING)
    @Column(name = "status", nullable = false, length = 10)
    private AgentStatus status;

    @Column(name = "changed_at", nullable = false)
    private Instant changedAt;

    protected ReportedAgentStatus() {
    }

    public ReportedAgentStatus(String vmId, AgentStatus status, Instant changedAt) {
        this.vmId = vmId;
        this.status = status;
        this.changedAt = changedAt;
    }

    public void change(AgentStatus status, Instant changedAt) {
        this.status = status;
        this.changedAt = changedAt;
    }

    public String getVmId() {
        return vmId;
    }

    public AgentStatus getStatus() {
        return status;
    }

    public Instant getChangedAt() {
        return changedAt;
    }
}
