package com.xenova.backend.sim.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.common.error.ApiException;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;

class SimCommandServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final AtomicLong now = new AtomicLong(5_000_000);
    private final SimStateTracker tracker = new SimStateTracker(objectMapper, now::get);
    private final List<JsonNode> sent = new ArrayList<>();
    private final AtomicBoolean connected = new AtomicBoolean(true);
    private final SimCommandService service = new SimCommandService(tracker, () -> (topic, payload, qos) -> {
        if (!connected.get()) {
            return false;
        }
        assertThat(topic).isEqualTo(SimCommandService.COMMAND_TOPIC);
        assertThat(qos).isEqualTo(1);
        try {
            sent.add(objectMapper.readTree(payload));
        } catch (java.io.IOException exception) {
            throw new IllegalStateException(exception);
        }
        return true;
    }, objectMapper, Clock.fixed(Instant.parse("2026-10-29T00:00:00Z"), ZoneOffset.UTC));
    private long seq;

    private void receive(String topic, String type, String payload) {
        tracker.accept(topic, """
                {"schema_version":"1.0","message_id":"m-%d","source_id":"simulator","boot_id":"b","agent_instance_id":"i",
                 "type":"%s","seq":%d,"sent_at":"2026-10-29T00:00:00.000Z","payload":%s}
                """.formatted(++seq, type, seq, payload).getBytes(StandardCharsets.UTF_8));
    }

    private void simulatorRunning(int obstacles) {
        StringBuilder list = new StringBuilder();
        for (int i = 0; i < obstacles; i++) {
            list.append(i == 0 ? "" : ",").append("{\"id\":\"o-").append(i).append("\",\"kind\":\"cone\",\"x\":1,\"y\":2,\"w\":0.18}");
        }
        receive(SimStateTracker.WORLD_TOPIC, "sim.world",
                "{\"revision\":3,\"track\":{\"id\":\"spielberg\"},\"obstacles\":[" + list + "],\"estop\":false}");
        receive(SimStateTracker.POSE_TOPIC, "sim.pose", "{\"x\":1,\"y\":2,\"yaw\":0,\"v_mps\":0,\"steer_rad\":0,\"world_revision\":3}");
    }

    private static void expect(Runnable call, HttpStatus status, String code) {
        assertThatThrownBy(call::run).isInstanceOfSatisfying(ApiException.class, e -> {
            assertThat(e.getStatus()).isEqualTo(status);
            assertThat(e.getCode()).isEqualTo(code);
        });
    }

    @Test
    void addObstacleSendsAContractEnvelopeAndReturnsIds() {
        simulatorRunning(0);
        Map<String, Object> body = service.addObstacle("tires", 1.5, -2.0);
        JsonNode envelope = sent.get(0);
        assertThat(envelope.path("type").asText()).isEqualTo("sim.command");
        assertThat(envelope.path("source_id").asText()).isEqualTo("dom0-server");
        assertThat(envelope.path("seq").asLong()).isEqualTo(1);
        JsonNode payload = envelope.path("payload");
        assertThat(payload.path("op").asText()).isEqualTo("obstacle.add");
        assertThat(payload.path("kind").asText()).isEqualTo("tires");
        assertThat(payload.path("x").asDouble()).isEqualTo(1.5);
        assertThat(payload.path("obstacle_id").asText()).isEqualTo(body.get("obstacle_id"));
        assertThat(payload.path("request_id").asText()).isEqualTo(body.get("request_id"));
    }

    @Test
    void kindDefaultsToCone() {
        simulatorRunning(0);
        service.addObstacle(null, 0, 0);
        assertThat(sent.get(0).path("payload").path("kind").asText()).isEqualTo("cone");
    }

    @Test
    void everyControlMapsToItsOp() {
        simulatorRunning(1);
        service.moveObstacle("o-0", 2, 3);
        service.removeObstacle("o-0");
        service.estop(true);
        service.push(-0.3);
        service.selectTrack("monza");
        service.raceStart();
        assertThat(sent.stream().map(e -> e.path("payload").path("op").asText()))
                .containsExactly("obstacle.move", "obstacle.remove", "estop", "push", "track.select", "race.start");
        assertThat(sent.get(2).path("payload").path("engaged").asBoolean()).isTrue();
        assertThat(sent.get(3).path("payload").path("dy_m").asDouble()).isEqualTo(-0.3);
        assertThat(sent.get(4).path("payload").path("track_id").asText()).isEqualTo("monza");
    }

    @Test
    void noSimulatorIs409() {
        expect(() -> service.addObstacle("cone", 0, 0), HttpStatus.CONFLICT, "SIM_UNAVAILABLE");
        simulatorRunning(0);
        now.addAndGet(1001); // pose older than one second
        expect(service::raceStart, HttpStatus.CONFLICT, "SIM_UNAVAILABLE");
        assertThat(sent).isEmpty();
    }

    @Test
    void unknownObstacleIs404() {
        simulatorRunning(1);
        expect(() -> service.moveObstacle("o-9", 0, 0), HttpStatus.NOT_FOUND, "OBSTACLE_NOT_FOUND");
        expect(() -> service.removeObstacle("o-9"), HttpStatus.NOT_FOUND, "OBSTACLE_NOT_FOUND");
    }

    @Test
    void rangesAre422() {
        simulatorRunning(8);
        expect(() -> service.addObstacle("cone", 0, 0), HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR");
        expect(() -> service.addObstacle("rock", 0, 0), HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR");
        expect(() -> service.push(0.6), HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR");
        expect(() -> service.selectTrack(" "), HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR");
        assertThat(sent).isEmpty();
    }

    @Test
    void brokerDownIs503() {
        simulatorRunning(0);
        connected.set(false);
        expect(() -> service.estop(false), HttpStatus.SERVICE_UNAVAILABLE, "MQTT_UNAVAILABLE");
    }
}
