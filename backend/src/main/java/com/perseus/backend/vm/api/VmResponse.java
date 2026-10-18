package com.perseus.backend.vm.api;

import com.perseus.backend.agent.domain.AgentStatus;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmState;

// agent_status is separate from state: a VM can be running while its agent is OFFLINE.
public record VmResponse(
        String vmId,
        String name,
        VmState state,
        AgentStatus agentStatus,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib) {

    static VmResponse of(Vm vm, AgentStatus agentStatus) {
        return new VmResponse(
                vm.vmId(),
                vm.name(),
                vm.state(),
                agentStatus,
                vm.templateId(),
                vm.templateVersion(),
                vm.vcpus(),
                vm.memoryMib());
    }
}
