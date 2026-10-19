package com.perseus.backend.vm.domain;

import com.perseus.backend.template.domain.GuestKind;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

// The VM specification Spring owns. dom0-manager does not keep name or template, so this is the source of truth
// for both the full VM list (including VMs that are off) and for restarting a VM with the same template.
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

    // Copied from the template at creation, so a later template config change does not reclassify the VM.
    // Rows created before this column existed are null and read as linux.
    @Enumerated(EnumType.STRING)
    @Column(name = "guest_kind", length = 10)
    private GuestKind guestKind;

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
        this.createdAt = createdAt;
    }

    public GuestKind getGuestKind() {
        return guestKind == null ? GuestKind.LINUX : guestKind;
    }

    public boolean hasSameSpec(CreateVmCommand command) {
        return templateId.equals(command.templateId())
                && templateVersion.equals(command.templateVersion())
                && vcpus == command.vcpus()
                && memoryMib == command.memoryMib();
    }

    // The same vm_id must always be launched with the same template (contract 4.2).
    public VmLaunchSpec launchSpec() {
        return new VmLaunchSpec(templateId, templateVersion, vcpus, memoryMib);
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
