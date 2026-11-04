package com.xenova.backend.drive.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.common.error.ApiException;
import java.io.IOException;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;

class DriveResumeRequesterTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final DriveStateTracker tracker = new DriveStateTracker(
            objectMapper, Clock.fixed(Instant.parse("2026-10-21T00:00:00Z"), ZoneOffset.UTC), () -> 0L);
    private final List<JsonNode> sent = new ArrayList<>();
    private final DriveInputPublisher publisher = (topic, payload, qos) -> {
        assertThat(topic).isEqualTo("xenova/v1/console/resume");
        assertThat(qos).isEqualTo(1);
        try {
            sent.add(objectMapper.readTree(payload));
        } catch (IOException exception) {
            throw new IllegalStateException(exception);
        }
        return true;
    };
    private long seq;

    DriveResumeRequesterTest() {
        tracker.mqttConnected();
    }

    private void control(String mode) {
        tracker.accept(DriveStateTracker.CONTROL_TOPIC, DriveStateTrackerTest.envelope("dom0-gateway", null,
                "vehicle.control", ++seq, "{\"mode\":\"%s\",\"generation\":1}".formatted(mode)));
    }

    private DriveResumeRequester requester(DriveInputPublisher target) {
        return new DriveResumeRequester(tracker, () -> target, objectMapper, Clock.systemUTC());
    }

    @Test
    void publishesTheRequestInAutoReady() {
        control("AUTO_READY");
        requester(publisher).request("operator");
        assertThat(sent).hasSize(1);
        JsonNode message = sent.get(0);
        assertThat(message.path("type").asText()).isEqualTo("drive.resume.requested");
        assertThat(message.path("source_id").asText()).isEqualTo("dom0-server");
        assertThat(message.path("schema_version").asText()).isEqualTo("1.0");
        assertThat(message.path("seq").asLong()).isEqualTo(1);
        assertThat(message.path("payload").path("requested_by").asText()).isEqualTo("operator");
        // Bound to the current control publication (verified gateway, contract §5.2)
        assertThat(message.path("causation_id").asText()).isEqualTo("m-1");
        assertThat(message.path("payload").path("expected_generation").asLong()).isEqualTo(1);
    }

    private void accepted(long generation) {
        tracker.accept("xenova/v1/apps/vehicle/events", DriveStateTrackerTest.envelope("dom0-gateway", "vehicle",
                "drive.resume.accepted", ++seq,
                "{\"switch_in_ms\":3000,\"generation\":%d,\"requested_by\":\"operator\"}".formatted(generation)));
    }

    @Test
    void cancelsTheAcceptedCountdown() {
        control("AUTO_READY");
        accepted(1);
        assertThat(tracker.snapshot().resume().requestedBy()).isEqualTo("operator");
        requester(publisher).cancel("admin");
        assertThat(sent).hasSize(1);
        JsonNode message = sent.get(0);
        assertThat(message.path("type").asText()).isEqualTo("drive.resume.cancel_requested");
        assertThat(message.path("causation_id").asText()).isEqualTo("m-1");
        assertThat(message.path("payload").path("expected_generation").asLong()).isEqualTo(1);
        assertThat(message.path("payload").path("requested_by").asText()).isEqualTo("admin");
    }

    @Test
    void cancelWithoutCountdownIs409() {
        control("AUTO_READY");
        assertThatThrownBy(() -> requester(publisher).cancel("admin"))
                .isInstanceOfSatisfying(ApiException.class, e -> {
                    assertThat(e.getStatus()).isEqualTo(HttpStatus.CONFLICT);
                    assertThat(e.getCode()).isEqualTo("NO_RESUME_COUNTDOWN");
                });
        assertThatThrownBy(() -> new DriveResumeRequester(new DriveStateTracker(objectMapper, Clock.systemUTC(),
                () -> 0L), () -> publisher, objectMapper, Clock.systemUTC()).cancel("admin"))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getCode())
                        .isEqualTo("DRIVE_STATE_UNAVAILABLE"));
        assertThat(sent).isEmpty();
    }

    @Test
    void refusesOutsideAutoReady() {
        assertThatThrownBy(() -> requester(publisher).request("operator"))
                .isInstanceOfSatisfying(ApiException.class, e -> {
                    assertThat(e.getStatus()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
                    assertThat(e.getCode()).isEqualTo("DRIVE_STATE_UNAVAILABLE");
                });
        control("MANUAL");
        assertThatThrownBy(() -> requester(publisher).request("operator"))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getCode()).isEqualTo("NOT_AUTO_READY"));
        assertThat(sent).isEmpty();
    }

    @Test
    void refusesAControlValueFromThePreviousMqttConnection() {
        control("AUTO_READY");
        tracker.mqttDisconnected();
        tracker.mqttConnected();
        assertThatThrownBy(() -> requester(publisher).request("operator"))
                .isInstanceOfSatisfying(ApiException.class, e -> {
                    assertThat(e.getStatus()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
                    assertThat(e.getCode()).isEqualTo("DRIVE_STATE_UNAVAILABLE");
                });
        assertThat(sent).isEmpty();
    }

    @Test
    void withoutBrokerIs503() {
        control("AUTO_READY");
        assertThatThrownBy(() -> requester(null).request("operator"))
                .isInstanceOfSatisfying(ApiException.class, e -> {
                    assertThat(e.getStatus()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE);
                    assertThat(e.getCode()).isEqualTo("MQTT_UNAVAILABLE");
                });
        assertThatThrownBy(() -> requester((topic, payload, qos) -> false).request("operator"))
                .isInstanceOfSatisfying(ApiException.class,
                        e -> assertThat(e.getStatus()).isEqualTo(HttpStatus.SERVICE_UNAVAILABLE));
    }
}
