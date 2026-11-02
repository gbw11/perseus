package com.xenova.backend.vm.api;

public record VmRegistrationRequest(
        String schemaVersion,
        Boolean ok,
        String requestId,
        String operationId,
        Boolean replayed,
        Data data,
        Object error) {

    public record Data(Boolean plan, Boolean executeRequired, Registration registration) {
    }

    public record Registration(
            String schemaVersion,
            String vmId,
            String name,
            String guestKind,
            String templateId,
            String templateVersion,
            Integer vcpus,
            Integer memoryMib,
            String assignedIpv4) {
    }
}

