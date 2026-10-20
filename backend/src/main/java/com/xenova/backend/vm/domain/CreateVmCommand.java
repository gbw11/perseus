package com.xenova.backend.vm.domain;

public record CreateVmCommand(
        String name,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib) {
}
