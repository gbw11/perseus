package com.xenova.backend.event.api;

import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.event.domain.VmEvent;
import java.time.Instant;
import java.util.Map;

// One event as REST-18 returns it; WS-02 sends the same object as its payload.
public record EventResponse(
        String eventId,
        EventType type,
        EventSeverity severity,
        String vmId,
        String operationId,
        Instant occurredAt,
        Instant recordedAt,
        Map<String, Object> payload) {

    public static EventResponse of(VmEvent event) {
        return new EventResponse(
                event.getEventId(),
                event.getType(),
                event.getSeverity(),
                event.getVmId(),
                event.getOperationId(),
                event.getOccurredAt(),
                event.getRecordedAt(),
                event.getPayload());
    }
}
