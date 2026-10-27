package com.xenova.backend.event.application;

import java.time.Instant;
import java.util.Objects;

// Immutable transport event. Contract validation and parsing subscribe to this boundary in the next step.
public record RecoveryEventReceived(byte[] payload, Instant receivedAt) {

    public RecoveryEventReceived {
        payload = Objects.requireNonNull(payload, "payload").clone();
        Objects.requireNonNull(receivedAt, "receivedAt");
    }

    @Override
    public byte[] payload() {
        return payload.clone();
    }
}
