package com.xenova.backend.vm.api;

import com.xenova.backend.agent.domain.AgentStatus;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.domain.Vm;
import com.xenova.backend.vm.domain.VmRole;
import com.xenova.backend.vm.domain.VmState;

// agent_status is separate from state: a VM can be running while its agent is OFFLINE.
// A zephyr guest has no agent, so its agent_status stays UNKNOWN and its ipv4 is null; guest_kind tells the screen why.
// ipv4 is the last address the agent reported, so it stays visible while the VM is off.
public record VmResponse(
        String vmId,
        String name,
        GuestKind guestKind,
        VmState state,
        AgentStatus agentStatus,
        String ipv4,
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
                vm.templateId(),
                vm.templateVersion(),
                vm.vcpus(),
                vm.memoryMib(),
                vm.role());
    }
}
