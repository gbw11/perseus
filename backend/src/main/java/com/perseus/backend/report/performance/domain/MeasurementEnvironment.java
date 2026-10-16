package com.perseus.backend.report.performance.domain;

import com.fasterxml.jackson.annotation.JsonValue;

// Kept on every trial so mock or QEMU numbers are never reported as real-board performance.
public enum MeasurementEnvironment {
    RPI4("rpi4"),
    QEMU("qemu"),
    MOCK("mock");

    private final String value;

    MeasurementEnvironment(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }
}
