package com.xenova.backend.vm.domain;

public record CreateVmCommand(
        String name,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib,
        VmRole role) {

    public CreateVmCommand(String name, String templateId, String templateVersion, int vcpus, int memoryMib) {
        this(name, templateId, templateVersion, vcpus, memoryMib, null);
    }
}
