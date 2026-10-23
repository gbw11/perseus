package com.xenova.backend.vm.domain;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Arrays;

// What the VM is for in the RC car scenario. The values are the MQTT app_id (MQTT contract §4), so the console
// can match a VM with its app events and monitor slot. Spring-only: dom0-manager never sees it.
public enum VmRole {
    AUTONOMOUS("autonomous"),
    MANUAL("manual"),
    MESSAGE_BOARD("message-board");

    /** Request validation pattern; keep in step with the values above. */
    public static final String PATTERN = "^(autonomous|manual|message-board)$";

    private final String value;

    VmRole(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    /** null stays null (no role). Callers validate with {@link #PATTERN} first. */
    public static VmRole fromValue(String value) {
        if (value == null) {
            return null;
        }
        return Arrays.stream(values())
                .filter(role -> role.value.equals(value))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("unknown role: " + value));
    }
}
