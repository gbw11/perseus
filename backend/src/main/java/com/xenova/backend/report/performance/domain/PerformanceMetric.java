package com.xenova.backend.report.performance.domain;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Arrays;
import java.util.Optional;

public enum PerformanceMetric {
    VM_BOOT_MS("vm_boot_ms"),
    VM_RESTART_MS("vm_restart_ms"),
    VM_RECOVERY_MS("vm_recovery_ms"),
    MESSAGE_RTT_MS("message_rtt_ms");

    private final String value;

    PerformanceMetric(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    public static Optional<PerformanceMetric> fromValue(String value) {
        return Arrays.stream(values()).filter(metric -> metric.value.equals(value)).findFirst();
    }
}
