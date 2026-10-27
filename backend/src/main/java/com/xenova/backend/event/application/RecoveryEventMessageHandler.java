package com.xenova.backend.event.application;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Instant;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Component;

// Entry point for recovery events delivered by MQTT-12. It never throws: malformed or contract-invalid
// messages are logged and dropped so one bad publication cannot tear down the shared MQTT subscription.
@Component
public class RecoveryEventMessageHandler {

    public static final String TOPIC = "xenova/v1/recovery/events";
    static final int MAX_PAYLOAD_BYTES = 64 * 1024;

    private static final Logger log = LoggerFactory.getLogger(RecoveryEventMessageHandler.class);

    private final ObjectMapper objectMapper;
    private final ApplicationEventPublisher eventPublisher;

    public RecoveryEventMessageHandler(ObjectMapper objectMapper, ApplicationEventPublisher eventPublisher) {
        this.objectMapper = objectMapper;
        this.eventPublisher = eventPublisher;
    }

    public Result handle(byte[] payload, Instant receivedAt) {
        try {
            RecoveryEvent event = parse(payload);
            validate(event);
            eventPublisher.publishEvent(new RecoveryEventReceived(event, receivedAt));
            return Result.ACCEPTED;
        } catch (Rejected rejected) {
            log.warn("Rejected recovery event on {}: {} {}", TOPIC, rejected.code, rejected.getMessage());
            return Result.REJECTED;
        } catch (RuntimeException exception) {
            log.error("Failed to handle recovery event on {}", TOPIC, exception);
            return Result.REJECTED;
        }
    }

    private RecoveryEvent parse(byte[] payload) {
        if (payload == null) {
            throw new Rejected("PAYLOAD_INVALID", "null payload");
        }
        if (payload.length > MAX_PAYLOAD_BYTES) {
            throw new Rejected("PAYLOAD_TOO_LARGE", payload.length + " bytes");
        }
        try {
            return objectMapper.readerFor(RecoveryEvent.class)
                    .with(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                    .with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                    .readValue(payload);
        } catch (java.io.IOException | IllegalArgumentException exception) {
            throw new Rejected("PAYLOAD_INVALID", "malformed JSON or field value");
        }
    }

    private static void validate(RecoveryEvent event) {
        if (event.eventId() == null || event.type() == null || event.vmId() == null
                || event.recoveryId() == null || event.attempt() == null || event.attempt() < 1
                || isBlank(event.reason()) || isBlank(event.mode())
                || event.detectedAt() == null || event.occurredAt() == null) {
            throw new Rejected("PAYLOAD_INVALID", "missing or invalid common field");
        }
        nonNegative("attempt_duration_ms", event.attemptDurationMs());
        nonNegative("recovery_duration_ms", event.recoveryDurationMs());

        if (event.recoveryAttemptId() == null
                && (event.type() != RecoveryEvent.Type.RECOVERED
                && event.type() != RecoveryEvent.Type.CANCELLED || event.attemptDurationMs() != null)) {
            throw new Rejected("PAYLOAD_INVALID",
                    "only recovery-level recovered/cancelled may omit recovery_attempt_id");
        }

        switch (event.type()) {
            case STARTED -> validateStarted(event);
            case COMPLETED -> validateCompleted(event);
            case RECOVERED -> validateRecovered(event);
            case CANCELLED -> validateCancelled(event);
            case FAILED -> validateFailed(event);
        }
    }

    private static void validateStarted(RecoveryEvent event) {
        if (event.attemptDurationMs() != null || event.recoveryDurationMs() != null
                || event.executionId() != null || event.finalAttempt() != null
                || event.nextRetryAt() != null || event.error() != null) {
            throw new Rejected("PAYLOAD_INVALID", "recovery.started carries terminal fields");
        }
    }

    private static void validateCompleted(RecoveryEvent event) {
        if (isBlank(event.executionId()) || event.recoveryDurationMs() == null
                || event.finalAttempt() != null || event.nextRetryAt() != null || event.error() != null) {
            throw new Rejected("PAYLOAD_INVALID", "invalid recovery.completed fields");
        }
    }

    private static void validateRecovered(RecoveryEvent event) {
        if (event.recoveryDurationMs() == null || event.executionId() != null
                || event.finalAttempt() != null || event.nextRetryAt() != null || event.error() != null) {
            throw new Rejected("PAYLOAD_INVALID", "invalid recovery.recovered fields");
        }
    }

    private static void validateCancelled(RecoveryEvent event) {
        if (event.recoveryDurationMs() == null || event.executionId() != null
                || event.finalAttempt() != null || event.nextRetryAt() != null || event.error() == null) {
            throw new Rejected("PAYLOAD_INVALID", "invalid recovery.cancelled fields");
        }
    }

    private static void validateFailed(RecoveryEvent event) {
        if (event.finalAttempt() == null || event.error() == null || event.executionId() != null) {
            throw new Rejected("PAYLOAD_INVALID", "recovery.failed requires final and error");
        }
        if (event.finalAttempt()
                && (event.recoveryDurationMs() == null || event.nextRetryAt() != null)) {
            throw new Rejected("PAYLOAD_INVALID", "invalid final recovery.failed fields");
        }
        if (!event.finalAttempt()
                && (event.nextRetryAt() == null || event.recoveryDurationMs() != null)) {
            throw new Rejected("PAYLOAD_INVALID", "invalid retryable recovery.failed fields");
        }
    }

    private static void nonNegative(String field, Long value) {
        if (value != null && value < 0) {
            throw new Rejected("PAYLOAD_INVALID", field + " must be non-negative");
        }
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    public enum Result {
        ACCEPTED,
        REJECTED
    }

    private static final class Rejected extends RuntimeException {

        private final String code;

        Rejected(String code, String detail) {
            super(detail);
            this.code = code;
        }
    }
}
