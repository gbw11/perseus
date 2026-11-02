package com.xenova.backend.vm.domain;

import com.xenova.backend.template.domain.GuestKind;

// Persistent platform-registry data used only to reconcile board VMs into Spring's vms table.
public record VmRegistrationInventory(
        String vmId,
        String name,
        GuestKind guestKind,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib,
        String assignedIpv4) {
}
