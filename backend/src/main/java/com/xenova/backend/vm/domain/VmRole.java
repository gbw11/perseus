package com.xenova.backend.vm.domain;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Arrays;

// Deployment binding for the current A/B drive topology. A drive VM can run both autonomous and manual apps;
// those app ids are not VM roles. Spring owns this label and dom0-manager never infers it from a VM name.
public enum VmRole {
    PRIMARY_DRIVE("primary_drive"),
    BACKUP_DRIVE("backup_drive"),
    INTERACTION("interaction");

    /** Request validation pattern; keep in step with the values above. */
    public static final String PATTERN = "^(primary_drive|backup_drive|interaction)$";

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
