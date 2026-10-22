package com.xenova.backend.vm.domain;

import com.xenova.backend.template.domain.GuestKind;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

// The public VM specification Spring owns. Retired rows remain here so a future restore can reuse the same
// permanent vm_id; the platform registry remains the source of truth for disk/runtime lifecycle.
@Entity
@Table(name = "vms")
public class VmRecord {

    @Id
    @Column(name = "vm_id", length = 36)
    private String vmId;

    @Column(nullable = false, unique = true, length = 63)
    private String name;

    @Column(name = "template_id", nullable = false, length = 64)
    private String templateId;

    @Column(name = "template_version", nullable = false, length = 64)
    private String templateVersion;

    @Column(nullable = false)
    private int vcpus;

    @Column(name = "memory_mib", nullable = false)
    private int memoryMib;

    @Column(name = "created_at", nullable = false)
    private Instant createdAt;

    @Enumerated(EnumType.STRING)
    @Column(length = 16)
    private VmLifecycle lifecycle;

    // Copied from the template at creation, so a later template config change does not reclassify the VM.
    // Rows created before this column existed are null and read as linux.
    @Enumerated(EnumType.STRING)
    @Column(name = "guest_kind", length = 10)
    private GuestKind guestKind;
    // Console-only label (VmRole). Not part of the launch spec, so a retried create ignores it.
    // Rows created before this column existed are null (no role).
    @Enumerated(EnumType.STRING)
    @Column(length = 16)
    private VmRole role;

    protected VmRecord() {
    }

    public VmRecord(String vmId, CreateVmCommand command, Instant createdAt) {
        this(vmId, command, GuestKind.LINUX, createdAt);
    }

    public VmRecord(String vmId, CreateVmCommand command, GuestKind guestKind, Instant createdAt) {
        this.vmId = vmId;
        this.name = command.name();
        this.templateId = command.templateId();
        this.templateVersion = command.templateVersion();
        this.vcpus = command.vcpus();
        this.memoryMib = command.memoryMib();
        this.guestKind = guestKind;
        this.role = command.role();
        this.createdAt = createdAt;
        this.lifecycle = VmLifecycle.REGISTERED;
    }

    public GuestKind getGuestKind() {
        return guestKind == null ? GuestKind.LINUX : guestKind;
    }

    public VmLifecycle getLifecycle() {
        return lifecycle == null ? VmLifecycle.REGISTERED : lifecycle;
    }

    public boolean isRegistered() {
        return getLifecycle() == VmLifecycle.REGISTERED;
    }

    public VmRole getRole() {
        return role;
    }

    public void changeRole(VmRole role) {
        this.role = role;
    }

    public void retire() {
        lifecycle = VmLifecycle.RETIRED;
    }

    public boolean hasSameSpec(CreateVmCommand command) {
        return templateId.equals(command.templateId())
                && templateVersion.equals(command.templateVersion())
                && vcpus == command.vcpus()
                && memoryMib == command.memoryMib();
    }

    // The same vm_id must always be launched with the same template (contract 4.2).
    public VmLaunchSpec launchSpec() {
        return new VmLaunchSpec(name, templateId, templateVersion, vcpus, memoryMib);
    }

    public String getVmId() {
        return vmId;
    }

    public String getName() {
        return name;
    }

    public String getTemplateId() {
        return templateId;
    }

    public String getTemplateVersion() {
        return templateVersion;
    }

    public int getVcpus() {
        return vcpus;
    }

    public int getMemoryMib() {
        return memoryMib;
    }

    public Instant getCreatedAt() {
        return createdAt;
    }
}
