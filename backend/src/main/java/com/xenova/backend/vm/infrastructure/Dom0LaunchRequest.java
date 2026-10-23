package com.xenova.backend.vm.infrastructure;

import com.xenova.backend.vm.domain.VmLaunchSpec;

// create의 표시 이름은 Spring 값을 그대로 전달한다. 추가 전송 필드는 OpenAPI 계약을 따른다.
record Dom0LaunchRequest(
        String vmId,
        String name,
        String templateId,
        String templateVersion,
        int vcpus,
        int memoryMib) {

    static Dom0LaunchRequest of(String vmId, VmLaunchSpec spec) {
        return new Dom0LaunchRequest(vmId, spec.name(), spec.templateId(), spec.templateVersion(), spec.vcpus(), spec.memoryMib());
    }
}
