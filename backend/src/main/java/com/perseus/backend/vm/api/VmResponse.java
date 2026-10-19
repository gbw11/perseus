package com.perseus.backend.vm.api;

import com.perseus.backend.agent.domain.AgentStatus;
import com.perseus.backend.template.domain.GuestKind;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmState;

// agent_status is separate from state: a VM can be running while its agent is OFFLINE.
// A zephyr guest has no agent, so its agent_status stays UNKNOWN; guest_kind tells the screen why.
public record VmResponse(
        String vmId,
        String name,
        GuestKind guestKind,
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
                vm.guestKind(),
                vm.state(),
                agentStatus,
                vm.templateId(),
                vm.templateVersion(),
                vm.vcpus(),
                vm.memoryMib());
    }
}
