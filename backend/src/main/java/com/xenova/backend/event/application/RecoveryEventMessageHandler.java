package com.xenova.backend.event.application;

import java.time.Instant;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Component;

// Entry point for recovery events delivered by MQTT-12. Contract validation, parsing and persistence
// are implemented behind this boundary so the shared MQTT connection only owns transport concerns.
@Component
public class RecoveryEventMessageHandler {

    public static final String TOPIC = "xenova/v1/recovery/events";

    private final ApplicationEventPublisher eventPublisher;

    public RecoveryEventMessageHandler(ApplicationEventPublisher eventPublisher) {
        this.eventPublisher = eventPublisher;
    }

    public void handle(byte[] payload, Instant receivedAt) {
        eventPublisher.publishEvent(new RecoveryEventReceived(payload, receivedAt));
    }
}
