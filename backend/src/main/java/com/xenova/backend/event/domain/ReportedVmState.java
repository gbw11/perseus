package com.xenova.backend.event.domain;

import com.xenova.backend.vm.domain.VmState;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

// Last VM state announced as an event (or first seen), so polling only reports real transitions
// and a restart does not repeat old ones.
@Entity
@Table(name = "reported_vm_states")
public class ReportedVmState {

    @Id
    @Column(name = "vm_id", length = 36)
    private String vmId;

    @Enumerated(EnumType.STRING)
    @Column(name = "state", nullable = false, length = 10)
    private VmState state;

    @Column(name = "changed_at", nullable = false)
    private Instant changedAt;

    protected ReportedVmState() {
    }

    public ReportedVmState(String vmId, VmState state, Instant changedAt) {
        this.vmId = vmId;
        this.state = state;
        this.changedAt = changedAt;
    }

    public void change(VmState state, Instant changedAt) {
        this.state = state;
        this.changedAt = changedAt;
    }

    public String getVmId() {
        return vmId;
    }

    public VmState getState() {
        return state;
    }

    public Instant getChangedAt() {
        return changedAt;
    }
}
