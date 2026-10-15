package com.perseus.backend.vm.domain;

public record Vm(
        String vmId,
        String name,
        VmState state,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib) {
}
