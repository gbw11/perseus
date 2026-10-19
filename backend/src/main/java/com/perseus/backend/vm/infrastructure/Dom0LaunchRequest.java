package com.perseus.backend.vm.infrastructure;

import com.perseus.backend.vm.domain.VmLaunchSpec;

// CreateVmRequest of the dom0-manager OpenAPI. It has no name field (additionalProperties: false).
record Dom0LaunchRequest(
        String vmId,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib) {

    static Dom0LaunchRequest of(String vmId, VmLaunchSpec spec) {
        return new Dom0LaunchRequest(vmId, spec.templateId(), spec.templateVersion(), spec.vcpus(), spec.memoryMib());
    }
}
