package com.perseus.backend.vm.domain;

// Sent to dom0-manager to create or restart a domain. The name stays in Spring.
public record VmLaunchSpec(String templateId, String templateVersion, int vcpus, int memoryMib) {
}
