package com.xenova.backend.event.application;

import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.event.domain.VmEvent;
import com.xenova.backend.event.domain.VmEventRepository;
import com.xenova.backend.event.domain.VmRecoveryHealth;
import com.xenova.backend.event.domain.VmRecoveryHealthRepository;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class RecoveryEventStore {

    private final VmEventRepository repository;
    private final VmRecoveryHealthRepository healthRepository;
    private final ApplicationEventPublisher eventPublisher;

    public RecoveryEventStore(
            VmEventRepository repository,
            VmRecoveryHealthRepository healthRepository,
            ApplicationEventPublisher eventPublisher) {
        this.repository = repository;
        this.healthRepository = healthRepository;
        this.eventPublisher = eventPublisher;
    }

    public boolean exists(UUID eventId) {
        return repository.existsByEventId(eventId.toString());
    }

    @Transactional
    public StoreResult store(RecoveryEvent source, Instant receivedAt) {
        String eventId = source.eventId().toString();
        if (repository.existsByEventId(eventId)) {
            return StoreResult.DUPLICATE;
        }

        VmEvent stored = repository.saveAndFlush(new VmEvent(
                eventId,
                eventType(source.type()),
                severity(source),
                source.vmId().toString(),
                source.recoveryId().toString(),
                source.recoveryAttemptId() == null ? null : source.recoveryAttemptId().toString(),
                source.occurredAt(),
                receivedAt,
                payload(source)));
        updateHealth(source, receivedAt);
        eventPublisher.publishEvent(new EventRecorded(stored));
        return StoreResult.STORED;
    }

    private void updateHealth(RecoveryEvent source, Instant receivedAt) {
        String vmId = source.vmId().toString();
        VmRecoveryHealth health = healthRepository.findById(vmId)
                .orElseGet(() -> new VmRecoveryHealth(vmId, receivedAt));
        boolean completed = source.type() == RecoveryEvent.Type.COMPLETED;
        boolean finalFailure = source.type() == RecoveryEvent.Type.FAILED
                && Boolean.TRUE.equals(source.finalAttempt());
        health.update(
                source.eventId().toString(),
                source.recoveryId().toString(),
                source.recoveryAttemptId() == null ? null : source.recoveryAttemptId().toString(),
                healthStatus(source),
                source.attempt(),
                completed,
                finalFailure,
                completed ? source.recoveryDurationMs() : null,
                source.occurredAt(),
                receivedAt);
        healthRepository.save(health);
    }

    private static String healthStatus(RecoveryEvent event) {
        return switch (event.type()) {
            case STARTED -> "STARTED";
            case COMPLETED -> "COMPLETED";
            case RECOVERED -> "RECOVERED";
            case CANCELLED -> "CANCELLED";
            case FAILED -> Boolean.TRUE.equals(event.finalAttempt()) ? "FAILED" : "RETRYING";
        };
    }

    private static EventType eventType(RecoveryEvent.Type type) {
        return switch (type) {
            case STARTED -> EventType.RECOVERY_STARTED;
            case COMPLETED -> EventType.RECOVERY_COMPLETED;
            case RECOVERED -> EventType.RECOVERY_RECOVERED;
            case CANCELLED -> EventType.RECOVERY_CANCELLED;
            case FAILED -> EventType.RECOVERY_FAILED;
        };
    }

    private static EventSeverity severity(RecoveryEvent event) {
        if (event.type() != RecoveryEvent.Type.FAILED) {
            return EventSeverity.INFO;
        }
        return Boolean.TRUE.equals(event.finalAttempt()) ? EventSeverity.ERROR : EventSeverity.WARNING;
    }

    private static Map<String, Object> payload(RecoveryEvent event) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("attempt", event.attempt());
        payload.put("reason", event.reason());
        payload.put("mode", event.mode());
        payload.put("detected_at", event.detectedAt().toString());
        put(payload, "attempt_duration_ms", event.attemptDurationMs());
        put(payload, "recovery_duration_ms", event.recoveryDurationMs());
        put(payload, "execution_id", event.executionId());
        put(payload, "final", event.finalAttempt());
        put(payload, "next_retry_at", event.nextRetryAt() == null ? null : event.nextRetryAt().toString());
        if (event.error() != null) {
            Map<String, Object> error = new LinkedHashMap<>();
            put(error, "code", event.error().code());
            put(error, "platform_code", event.error().platformCode());
            put(error, "message", event.error().message());
            payload.put("error", error);
        }
        return payload;
    }

    private static void put(Map<String, Object> target, String key, Object value) {
        if (value != null) {
            target.put(key, value);
        }
    }

    public enum StoreResult {
        STORED,
        DUPLICATE
    }
}
