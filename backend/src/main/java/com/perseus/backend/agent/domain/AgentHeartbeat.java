package com.perseus.backend.agent.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

// Last heartbeat received from the agent inside a VM (MQTT-01). One row per VM.
@Entity
@Table(name = "agent_heartbeats")
public class AgentHeartbeat {

    @Id
    @Column(name = "vm_id", length = 36)
    private String vmId;

    @Column(name = "boot_id", length = 64)
    private String bootId;

    @Column(name = "agent_instance_id", length = 36)
    private String agentInstanceId;

    @Column(name = "seq")
    private Long seq;

    @Column(name = "sent_at")
    private Instant sentAt;

    @Column(name = "received_at", nullable = false)
    private Instant receivedAt;

    protected AgentHeartbeat() {
    }

    public AgentHeartbeat(String vmId) {
        this.vmId = vmId;
    }

    // Duplicates and delayed messages must not extend the liveness of the agent.
    public boolean isSupersededBy(String agentInstanceId, long seq, Instant sentAt) {
        if (agentInstanceId != null && agentInstanceId.equals(this.agentInstanceId)) {
            return this.seq == null || seq > this.seq;
        }
        return this.sentAt == null || sentAt == null || sentAt.isAfter(this.sentAt);
    }

    public void update(String bootId, String agentInstanceId, long seq, Instant sentAt, Instant receivedAt) {
        this.bootId = bootId;
        this.agentInstanceId = agentInstanceId;
        this.seq = seq;
        this.sentAt = sentAt;
        this.receivedAt = receivedAt;
    }

    public String getVmId() {
        return vmId;
    }

    public String getBootId() {
        return bootId;
    }

    public String getAgentInstanceId() {
        return agentInstanceId;
    }

    public Long getSeq() {
        return seq;
    }

    public Instant getSentAt() {
        return sentAt;
    }

    public Instant getReceivedAt() {
        return receivedAt;
    }
}
