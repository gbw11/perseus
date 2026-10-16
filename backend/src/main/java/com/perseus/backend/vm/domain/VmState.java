package com.perseus.backend.vm.domain;

import com.fasterxml.jackson.annotation.JsonCreator;
import com.fasterxml.jackson.annotation.JsonValue;

public enum VmState {
    RUNNING("running"),
    PAUSED("paused"),
    STOPPING("stopping"),
    SHUTDOWN("shutdown"),
    CRASHED("crashed");

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
