package com.xenova.backend.drive.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.event.application.EventRecorder;
import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import java.util.LinkedHashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

// Stores the gateway's mode changes and faults in the event log so they reach the timeline (REST-18, WS-02).
// Severity follows MQTT contract §6: a mode change is INFO, entering SAFE_STOP and any fault are WARNING.
@Service
public class DriveEventLogger {

    private static final Logger log = LoggerFactory.getLogger(DriveEventLogger.class);

    private final EventRecorder eventRecorder;
    private final ObjectMapper objectMapper;

    public DriveEventLogger(EventRecorder eventRecorder, ObjectMapper objectMapper) {
        this.eventRecorder = eventRecorder;
        this.objectMapper = objectMapper;
    }

    public void record(DriveNotice notice) {
        try {
            switch (notice.type()) {
                case "drive.mode.changed" -> modeChanged(notice);
                case "drive.fault.detected" -> faultDetected(notice);
                default -> log.debug("Ignoring drive notice {}", notice.type());
            }
        } catch (RuntimeException exception) {
            log.error("Failed to record drive notice {}", notice.type(), exception);
        }
    }

    private void modeChanged(DriveNotice notice) {
        JsonNode body = notice.payload();
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("from", text(body, "from"));
        payload.put("to", text(body, "to"));
        payload.put("generation", body.path("generation").isIntegralNumber() ? body.path("generation").asLong() : null);
        payload.put("reason", text(body, "reason"));
        payload.put("source", notice.publisher());
        EventSeverity severity = "SAFE_STOP".equals(text(body, "to")) ? EventSeverity.WARNING : EventSeverity.INFO;
        eventRecorder.record(EventType.DRIVE_MODE_CHANGED, severity, null, null, payload);
    }

    private void faultDetected(DriveNotice notice) {
        JsonNode body = notice.payload();
        @SuppressWarnings("unchecked")
        Map<String, Object> payload = objectMapper.convertValue(body, LinkedHashMap.class);
        payload.put("source", notice.publisher());
        eventRecorder.record(EventType.DRIVE_FAULT_DETECTED, EventSeverity.WARNING, text(body, "vm_id"), null, payload);
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value != null && value.isTextual() ? value.asText() : null;
    }
}
