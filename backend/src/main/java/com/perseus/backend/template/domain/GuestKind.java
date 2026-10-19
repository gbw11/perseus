package com.perseus.backend.template.domain;

import com.fasterxml.jackson.annotation.JsonValue;

// What runs inside the VM. A Zephyr guest is a console-only firmware: no VM agent, no network,
// no graceful shutdown, so the screen and reports treat it differently from a Linux guest.
// Spring derives it from its own template config; it is not part of the dom0-manager contract.
public enum GuestKind {
    LINUX("linux"),
    ZEPHYR("zephyr");

    private final String value;

    GuestKind(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }
}
