package com.xenova.backend.vm.api;

public record VmRegistrationResponse(String vmId, String registration) {

    public static VmRegistrationResponse confirmed(String vmId) {
        return new VmRegistrationResponse(vmId, "confirmed");
    }
}

