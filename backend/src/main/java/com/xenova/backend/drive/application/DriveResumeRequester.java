package com.xenova.backend.drive.application;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.common.error.ApiException;
import java.time.Clock;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

// POST /api/v1/drive/resume → drive.resume.requested, DELETE → drive.resume.cancel_requested, both on
// xenova/v1/console/resume (QoS 1). The verified gateway only takes a request bound to the vehicle.control it
// published: envelope causation_id = that control's message_id, payload expected_generation = its generation
// (contract §5.2). The backend refuses requests that cannot apply up front so the operator sees why; the gateway
// still has the final say and answers with drive.resume.accepted / cancelled.
@Service
public class DriveResumeRequester {

    public static final String RESUME_TOPIC = DriveStateTracker.PREFIX + "console/resume";
    static final String TYPE = "drive.resume.requested";
    static final String CANCEL_TYPE = "drive.resume.cancel_requested";

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

    // Modes in which the verified gateway takes drive.resume.requested (contract §5.2): AUTO_READY starts the
    // countdown to AUTO, and MANUAL/AUTO use the same request to rearm an output-prohibited (disarmed) mode, unlike
    // the legacy AUTO_READY-only rule. SAFE_STOP takes it too: the gateway (verified.on_resume) picks a ready manual
    // VM and rearms MANUAL. vehicle.control does not carry output_permitted, so the backend cannot tell a disarmed
    // mode from an armed one; the gateway ignores a request that does not apply (202 means "sent").
    static final Set<String> RESUMABLE_MODES = Set.of("AUTO_READY", "MANUAL", "AUTO", "SAFE_STOP");

    public synchronized void request(String requestedBy) {
        DriveState state = tracker.snapshot();
        DriveState.Control control = freshControl(state, "자율주행 재개를 요청할 수 없습니다.");
        if (!RESUMABLE_MODES.contains(control.mode())) {
            throw new ApiException(HttpStatus.CONFLICT, "NOT_AUTO_READY",
                    "재개·재무장은 AUTO_READY·MANUAL·AUTO·SAFE_STOP 상태에서만 요청할 수 있습니다. 현재: "
                            + control.mode());
        }
        DriveStateTracker.ControlContext context = context(control, "자율주행 재개를 요청할 수 없습니다.");
        send(TYPE, context.messageId(), context.generation(), requestedBy, "재개 요청을");
    }

    // Cancel the countdown the gateway accepted (contract v1.7). The mode stays as it is; AUTO needs a new request.
    public synchronized void cancel(String requestedBy) {
        DriveState state = tracker.snapshot();
        DriveState.Control control = freshControl(state, "재개 취소를 요청할 수 없습니다.");
        DriveState.Resume resume = state.resume();
        if (resume == null || resume.generation() == null) {
            throw new ApiException(HttpStatus.CONFLICT, "NO_RESUME_COUNTDOWN",
                    "취소할 자율주행 재개 카운트다운이 없습니다.");
        }
        DriveStateTracker.ControlContext context = context(control, "재개 취소를 요청할 수 없습니다.");
        // The gateway checks the generation the countdown started in, which is also the current one.
        send(CANCEL_TYPE, context.messageId(), resume.generation(), requestedBy, "재개 취소 요청을");
    }

    private static DriveState.Control freshControl(DriveState state, String what) {
        DriveState.Control control = state.control();
        if (control == null || control.source().status() != DriveState.SourceStatus.FRESH) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "DRIVE_STATE_UNAVAILABLE",
                    "신선한 주행 상태를 확인할 수 없어 " + what);
        }
        return control;
    }

    // The control publication the request refers to. It must be the one the snapshot was judged on.
    private DriveStateTracker.ControlContext context(DriveState.Control control, String what) {
        DriveStateTracker.ControlContext context = tracker.controlContext().orElse(null);
        if (context == null || context.generation() == null || !context.generation().equals(control.generation())) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "DRIVE_STATE_UNAVAILABLE",
                    "현재 차량 제어 문맥을 확인할 수 없어 " + what);
        }
        return context;
    }

    private void send(String type, String causationId, long expectedGeneration, String requestedBy, String what) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("expected_generation", expectedGeneration);
        payload.put("requested_by", requestedBy);
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schema_version", DriveStateTracker.SCHEMA_VERSION);
        envelope.put("message_id", UUID.randomUUID().toString());
        envelope.put("causation_id", causationId);
        envelope.put("source_id", ManualInputRelay.SOURCE_ID);
        envelope.put("boot_id", bootId);
        envelope.put("agent_instance_id", instanceId);
        envelope.put("type", type);
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
                    "MQTT 브로커에 연결되어 있지 않아 " + what + " 보내지 못했습니다.");
        }
    }
}
