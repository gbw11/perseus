package com.xenova.backend.vm.domain;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

public enum VmState {
    // Reported by dom0-manager from `xl list` (vm-api-contract-v1 3.1).
    RUNNING("running"),
    STOPPING("stopping"),
    PAUSED("paused"),
    CRASHED("crashed"),
    // Spring-only: the VM is recorded but has no Xen domain. dom0-manager never returns it.
    STOPPED("stopped");

    private final String value;

    VmState(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    @JsonCreator
    public static VmState fromValue(String value) {
        for (VmState state : values()) {
            if (state.value.equals(value)) {
                return state;
            }
        }
        throw new IllegalArgumentException("Unknown VM state: " + value);
    }
}
