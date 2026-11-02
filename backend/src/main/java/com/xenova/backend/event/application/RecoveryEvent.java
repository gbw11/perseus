package com.xenova.backend.event.application;

import java.time.Instant;
import java.util.UUID;

// MQTT-12 / VM API contract 2.5. Field names are mapped to snake_case by the application ObjectMapper.
public record RecoveryEvent(
        UUID eventId,
        Type type,
        UUID vmId,
        UUID recoveryId,
        UUID recoveryAttemptId,
        Integer attempt,
        String reason,
        String mode,
        Instant detectedAt,
        Instant occurredAt,
        Long attemptDurationMs,
        Long recoveryDurationMs,
        String executionId,
        @com.fasterxml.jackson.annotation.JsonProperty("final") Boolean finalAttempt,
        Instant nextRetryAt,
        Error error) {

    public enum Type {
        STARTED("recovery.started"),
        COMPLETED("recovery.completed"),
        RECOVERED("recovery.recovered"),
        CANCELLED("recovery.cancelled"),
        FAILED("recovery.failed");

        private final String value;

        Type(String value) {
            this.value = value;
        }

        @com.fasterxml.jackson.annotation.JsonValue
        public String value() {
            return value;
        }

        @com.fasterxml.jackson.annotation.JsonCreator
        public static Type fromValue(String value) {
            for (Type type : values()) {
                if (type.value.equals(value)) {
                    return type;
                }
            }
            throw new IllegalArgumentException("unknown recovery event type " + value);
        }
    }

    public record Error(String code, String platformCode, String message) {
    }
}
