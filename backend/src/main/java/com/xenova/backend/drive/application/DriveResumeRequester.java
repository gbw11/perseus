package com.xenova.backend.drive.application;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.common.error.ApiException;
import java.time.Clock;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

// POST /api/v1/drive/resume → MQTT-13 drive.resume.requested on xenova/v1/console/resume (QoS 1).
// The gateway only accepts it in AUTO_READY (contract §6), so the backend refuses other modes up front and the
// operator sees why; the gateway still has the final say and answers with drive.resume.accepted / cancelled.
@Service
public class DriveResumeRequester {

    public static final String RESUME_TOPIC = DriveStateTracker.PREFIX + "console/resume";
    static final String TYPE = "drive.resume.requested";

    // Absent when MQTT is disabled (xenova.mqtt.enabled=false); requests are then answered 503.
    private final Supplier<DriveInputPublisher> publisher;
    private final DriveStateTracker tracker;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final String bootId = UUID.randomUUID().toString();
    private final String instanceId = UUID.randomUUID().toString();
    private long seq;

    @Autowired
    public DriveResumeRequester(
            DriveStateTracker tracker, ObjectProvider<DriveInputPublisher> publisher, ObjectMapper objectMapper,
            Clock clock) {
        this(tracker, publisher::getIfAvailable, objectMapper, clock);
    }

    DriveResumeRequester(
            DriveStateTracker tracker, Supplier<DriveInputPublisher> publisher, ObjectMapper objectMapper, Clock clock) {
        this.tracker = tracker;
        this.publisher = publisher;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    public synchronized void request(String requestedBy) {
        DriveState.Control control = tracker.snapshot().control();
        if (control == null || control.source().status() != DriveState.SourceStatus.FRESH) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "DRIVE_STATE_UNAVAILABLE",
                    "신선한 주행 상태를 확인할 수 없어 자율주행 재개를 요청할 수 없습니다.");
        }
        if (!"AUTO_READY".equals(control.mode())) {
            throw new ApiException(HttpStatus.CONFLICT, "NOT_AUTO_READY",
                    "자율주행 재개는 AUTO_READY 상태에서만 할 수 있습니다. 현재: "
                            + control.mode());
        }
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("requested_by", requestedBy);
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schema_version", DriveStateTracker.SCHEMA_VERSION);
        envelope.put("message_id", UUID.randomUUID().toString());
        envelope.put("source_id", ManualInputRelay.SOURCE_ID);
        envelope.put("boot_id", bootId);
        envelope.put("agent_instance_id", instanceId);
        envelope.put("type", TYPE);
        envelope.put("seq", ++seq);
        envelope.put("sent_at", DateTimeFormatter.ISO_INSTANT.format(clock.instant().truncatedTo(ChronoUnit.MILLIS)));
        envelope.put("payload", payload);
        byte[] bytes;
        try {
            bytes = objectMapper.writeValueAsBytes(envelope);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException(exception);
        }
        DriveInputPublisher target = publisher.get();
        if (target == null || !target.publish(RESUME_TOPIC, bytes, 1)) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "MQTT_UNAVAILABLE",
                    "MQTT 브로커에 연결되어 있지 않아 재개 요청을 보내지 못했습니다.");
        }
    }
}
