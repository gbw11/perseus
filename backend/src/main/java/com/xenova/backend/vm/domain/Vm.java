package com.xenova.backend.vm.domain;

import com.xenova.backend.template.domain.GuestKind;

// Public VM view: Spring's record combined with the live Xen state from dom0-manager (contract 2.2).
public record Vm(
        String vmId,
        String name,
        GuestKind guestKind,
        VmState state,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib,
        VmRole role) {

    public Vm(String vmId, String name, GuestKind guestKind, VmState state, String templateId, String templateVersion,
            int vcpus, int memoryMib) {
        this(vmId, name, guestKind, state, templateId, templateVersion, vcpus, memoryMib, null);
    }

    public static Vm of(VmRecord record, VmRuntime runtime) {
        if (runtime == null) {
            return new Vm(record.getVmId(), record.getName(), record.getGuestKind(), VmState.STOPPED,
                    record.getTemplateId(), record.getTemplateVersion(), record.getVcpus(), record.getMemoryMib(),
                    record.getRole());
        }
        return new Vm(record.getVmId(), record.getName(), record.getGuestKind(), runtime.state(),
                record.getTemplateId(), record.getTemplateVersion(), runtime.vcpus(), runtime.memoryMib(),
                record.getRole());
    }
}
