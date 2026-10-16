package com.perseus.backend.agent.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Convert;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Lob;
import jakarta.persistence.Table;
import java.time.Instant;
import java.util.List;

// Latest top-process list sent with the metrics, served later by REST-13. One row per VM.
@Entity
@Table(name = "process_snapshots")
public class ProcessSnapshot {

    @Id
    @Column(name = "vm_id", length = 36)
    private String vmId;

    @Column(name = "boot_id", length = 64)
    private String bootId;

    @Column(name = "observed_at", nullable = false)
    private Instant observedAt;

    @Column(name = "received_at", nullable = false)
    private Instant receivedAt;

    @Lob
    @Convert(converter = ProcessInfosConverter.class)
    @Column(name = "processes")
    private List<ProcessInfo> processes;

    protected ProcessSnapshot() {
    }

    public ProcessSnapshot(String vmId) {
        this.vmId = vmId;
    }

    public void update(String bootId, List<ProcessInfo> processes, Instant observedAt, Instant receivedAt) {
        this.bootId = bootId;
        this.processes = processes;
        this.observedAt = observedAt;
        this.receivedAt = receivedAt;
    }

    public String getVmId() {
        return vmId;
    }

    public String getBootId() {
        return bootId;
    }

    public Instant getObservedAt() {
        return observedAt;
    }

    public Instant getReceivedAt() {
        return receivedAt;
    }

    public List<ProcessInfo> getProcesses() {
        return processes == null ? List.of() : processes;
    }
}
