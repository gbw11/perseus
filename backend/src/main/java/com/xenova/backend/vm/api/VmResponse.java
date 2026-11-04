package com.xenova.backend.vm.api;

import com.xenova.backend.agent.domain.AgentStatus;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.domain.Vm;
import com.xenova.backend.vm.domain.VmRole;
import com.xenova.backend.vm.domain.VmState;

// agent_status is separate from state: a VM can be running while its agent is OFFLINE.
// ipv4 is the last Linux Agent report. assignedIpv4 comes from registration and is available while stopped.
public record VmResponse(
        String vmId,
        String name,
        GuestKind guestKind,
        VmState state,
        AgentStatus agentStatus,
        String ipv4,
        String assignedIpv4,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib,
        VmRole role) {

    static VmResponse of(Vm vm, AgentStatus agentStatus, String ipv4) {
        return new VmResponse(
                vm.vmId(),
                vm.name(),
                vm.guestKind(),
                vm.state(),
                agentStatus,
                vm.guestKind() == GuestKind.ZEPHYR ? null : ipv4,
                vm.assignedIpv4(),
                vm.templateId(),
                vm.templateVersion(),
                vm.vcpus(),
                vm.memoryMib(),
                vm.role());
    }
}
