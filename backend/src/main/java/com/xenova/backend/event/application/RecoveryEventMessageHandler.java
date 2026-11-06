package com.xenova.backend.event.application;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Instant;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.stereotype.Component;

// Entry point for recovery events delivered by MQTT-12. It never throws: malformed or contract-invalid
// messages are logged and dropped so one bad publication cannot tear down the shared MQTT subscription.
@Component
public class RecoveryEventMessageHandler {

    public static final String TOPIC = "xenova/v1/recovery/events";
    static final int MAX_PAYLOAD_BYTES = 64 * 1024;
    static final String DOM0_SOURCE_ID = "dom0-server";

    private static final Logger log = LoggerFactory.getLogger(RecoveryEventMessageHandler.class);

    private final ObjectMapper objectMapper;
    private final RecoveryEventStore eventStore;

    public RecoveryEventMessageHandler(ObjectMapper objectMapper, RecoveryEventStore eventStore) {
        this.objectMapper = objectMapper;
        this.eventStore = eventStore;
    }

    public Result handle(byte[] payload, Instant receivedAt) {
        try {
            RecoveryEvent event = parse(payload);
            return handle(event, receivedAt);
        } catch (Rejected rejected) {
            log.warn("Rejected recovery event on {}: {} {}", TOPIC, rejected.code, rejected.getMessage());
            return Result.REJECTED;
        } catch (DataIntegrityViolationException duplicateRace) {
            RecoveryEvent event = parse(payload);
            if (event.eventId() != null && eventStore.exists(event.eventId())) {
                log.debug("Ignored concurrently duplicated recovery event {} on {}", event.eventId(), TOPIC);
                return Result.DUPLICATE;
            }
            log.error("Failed to persist recovery event on {}", TOPIC, duplicateRace);
            return Result.REJECTED;
        } catch (RuntimeException exception) {
            log.error("Failed to handle recovery event on {}", TOPIC, exception);
            return Result.REJECTED;
        }
    }

    public Result handle(RecoveryEvent event, Instant receivedAt) {
        try {
            validate(event);
            RecoveryEventStore.StoreResult stored = eventStore.store(event, receivedAt);
            return stored == RecoveryEventStore.StoreResult.DUPLICATE ? Result.DUPLICATE : Result.ACCEPTED;
        } catch (Rejected rejected) {
            log.warn("Rejected recovery event from backfill: {} {}", rejected.code, rejected.getMessage());
            return Result.REJECTED;
        } catch (DataIntegrityViolationException duplicateRace) {
            if (event.eventId() != null && eventStore.exists(event.eventId())) {
                log.debug("Ignored concurrently duplicated recovery event {} from backfill", event.eventId());
                return Result.DUPLICATE;
            }
            log.error("Failed to persist recovery event from backfill", duplicateRace);
            return Result.REJECTED;
        } catch (RuntimeException exception) {
            log.error("Failed to handle recovery event from backfill", exception);
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
            JsonNode root = objectMapper.reader()
                    .with(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                    .readTree(payload);
            return objectMapper.readerFor(RecoveryEvent.class)
                    .with(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                    .readValue(eventObject(root));
        } catch (java.io.IOException | IllegalArgumentException exception) {
            throw new Rejected("PAYLOAD_INVALID", "malformed JSON or field value");
        }
    }

    // MQTT contract 11: the Worker publishes the event inside the common envelope (source_id dom0-server),
    // as xenova_mqtt.publish() does. A bare event object is still accepted for older publishers and tests.
    private static JsonNode eventObject(JsonNode root) {
        if (root == null || !root.isObject()) {
            throw new Rejected("PAYLOAD_INVALID", "payload is not a JSON object");
        }
        if (!root.has("schema_version") || !root.has("payload")) {
            return root;
        }
        JsonNode event = root.get("payload");
        if (!event.isObject()) {
            throw new Rejected("PAYLOAD_INVALID", "envelope payload is not an object");
        }
        if (root.hasNonNull("source_id") && !DOM0_SOURCE_ID.equals(root.get("source_id").asText())) {
            throw new Rejected("SOURCE_INVALID", "unexpected source_id " + root.get("source_id").asText());
        }
        if (root.hasNonNull("type") && event.hasNonNull("type")
                && !root.get("type").asText().equals(event.get("type").asText())) {
            throw new Rejected("PAYLOAD_INVALID", "envelope type differs from event type");
        }
        return event;
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
        DUPLICATE,
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
