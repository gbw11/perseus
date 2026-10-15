package com.perseus.backend.vm.infrastructure;

import com.perseus.backend.vm.domain.CreateVmCommand;

record Dom0CreateVmRequest(
        String vmId,
        String name,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib) {

    static Dom0CreateVmRequest of(String vmId, CreateVmCommand command) {
        return new Dom0CreateVmRequest(
                vmId,
                command.name(),
                command.templateId(),
                command.templateVersion(),
                command.vcpus(),
                command.memoryMib());
    }
}
