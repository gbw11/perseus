package com.xenova.backend.vm.domain;

// Spring 표시 이름을 create 경로로 전달한다. UUID 기반 실행 신원과는 별개다.
public record VmLaunchSpec(String name, String templateId, String templateVersion, int vcpus, int memoryMib) {
}
