package com.perseus.backend.vm.domain;

// Public VM view: Spring's record combined with the live Xen state from dom0-manager (contract 2.2).
public record Vm(
        String vmId,
        String name,
        VmState state,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib) {

    public static Vm of(VmRecord record, VmRuntime runtime) {
        if (runtime == null) {
            return new Vm(record.getVmId(), record.getName(), VmState.STOPPED, record.getTemplateId(),
                    record.getTemplateVersion(), record.getVcpus(), record.getMemoryMib());
        }
        return new Vm(record.getVmId(), record.getName(), runtime.state(), record.getTemplateId(),
                record.getTemplateVersion(), runtime.vcpus(), runtime.memoryMib());
    }
}
