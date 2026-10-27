package com.xenova.backend.event.application;

import java.time.Instant;
import java.util.Objects;

// Published only after an MQTT-12 payload has passed the recovery event contract.
public record RecoveryEventReceived(RecoveryEvent event, Instant receivedAt) {

    public RecoveryEventReceived {
        Objects.requireNonNull(event, "event");
        Objects.requireNonNull(receivedAt, "receivedAt");
    }
}
