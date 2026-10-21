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
    }

    @Test
    void refusesOutsideAutoReady() {
        assertThatThrownBy(() -> requester(publisher).request("operator"))
                .isInstanceOfSatisfying(ApiException.class, e -> {
                    assertThat(e.getStatus()).isEqualTo(HttpStatus.CONFLICT);
                    assertThat(e.getCode()).isEqualTo("NOT_AUTO_READY");
                });
        control("MANUAL");
        assertThatThrownBy(() -> requester(publisher).request("operator"))
                .isInstanceOfSatisfying(ApiException.class, e -> assertThat(e.getCode()).isEqualTo("NOT_AUTO_READY"));
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
