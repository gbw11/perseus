package com.xenova.backend.event.domain;

import java.util.Optional;

public enum EventSeverity {
    INFO,
    WARNING,
    ERROR;

    public static Optional<EventSeverity> fromValue(String value) {
        for (EventSeverity severity : values()) {
            if (severity.name().equals(value)) {
                return Optional.of(severity);
            }
        }
        return Optional.empty();
    }
}
