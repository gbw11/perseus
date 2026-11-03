package com.xenova.backend.sim.application;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
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

// Console simulator controls REST-31~37 → MQTT-17 sim.command (MQTT contract §12.2, S15P31E110-271).
// The backend checks what it can know (simulator present, obstacle id in the last sim.world, field ranges) and
// answers 202 once the command is handed to the broker. Whether it was applied arrives later as MQTT-18
// sim.command.result with the same request_id, relayed on WS-06; placement rules (ON_CAR, OUT_OF_TRACK) are the
// simulator's to judge.
@Service
public class SimCommandService {

    public static final String COMMAND_TOPIC = "xenova/v1/sim/command";
    public static final Set<String> KINDS = Set.of("cone", "box", "tires");
    static final int MAX_OBSTACLES = 8;
    static final double PUSH_MAX_M = 0.5;
    /** Contract §2: non-VM publishers use source_id; the backend is the dom0-server source (same as manual input). */
    static final String SOURCE_ID = "dom0-server";
    static final String SCHEMA_VERSION = "1.0";

    private final SimStateTracker tracker;
    private final Supplier<SimCommandPublisher> publisher;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final String bootId = UUID.randomUUID().toString();
    private final String instanceId = UUID.randomUUID().toString();
    private long seq;

    @Autowired
    public SimCommandService(SimStateTracker tracker, ObjectProvider<SimCommandPublisher> publisher,
            ObjectMapper objectMapper, Clock clock) {
        this(tracker, publisher::getIfAvailable, objectMapper, clock);
    }

    SimCommandService(SimStateTracker tracker, Supplier<SimCommandPublisher> publisher, ObjectMapper objectMapper,
            Clock clock) {
        this.tracker = tracker;
        this.publisher = publisher;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    /** REST-31. Returns {obstacle_id, request_id}. */
    public Map<String, Object> addObstacle(String kind, double x, double y) {
        String chosen = kind == null ? "cone" : kind;
        if (!KINDS.contains(chosen)) {
            throw invalid("모르는 장애물 종류입니다: " + chosen);
        }
        SimState state = requireAvailable();
        if (obstacleCount(state) >= MAX_OBSTACLES) {
            throw invalid("장애물은 " + MAX_OBSTACLES + "개까지 놓을 수 있습니다.");
        }
        String obstacleId = "o-" + UUID.randomUUID().toString().substring(0, 8);
        Map<String, Object> command = command("obstacle.add");
        command.put("obstacle_id", obstacleId);
        command.put("kind", chosen);
        command.put("x", x);
        command.put("y", y);
        String requestId = send(command);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("obstacle_id", obstacleId);
        body.put("request_id", requestId);
        return body;
    }

    /** REST-32. */
    public Map<String, Object> moveObstacle(String obstacleId, double x, double y) {
        requireObstacle(requireAvailable(), obstacleId);
        Map<String, Object> command = command("obstacle.move");
        command.put("obstacle_id", obstacleId);
        command.put("x", x);
        command.put("y", y);
        return Map.of("request_id", send(command));
    }

    /** REST-33. */
    public Map<String, Object> removeObstacle(String obstacleId) {
        requireObstacle(requireAvailable(), obstacleId);
        Map<String, Object> command = command("obstacle.remove");
        command.put("obstacle_id", obstacleId);
        return Map.of("request_id", send(command));
    }

    /** REST-34. */
    public Map<String, Object> estop(boolean engaged) {
        requireAvailable();
        Map<String, Object> command = command("estop");
        command.put("engaged", engaged);
        return Map.of("request_id", send(command));
    }

    /** REST-35. */
    public Map<String, Object> push(double dyM) {
        if (!Double.isFinite(dyM) || Math.abs(dyM) > PUSH_MAX_M) {
            throw invalid("차는 옆으로 " + PUSH_MAX_M + " m 까지만 밀 수 있습니다.");
        }
        requireAvailable();
        Map<String, Object> command = command("push");
        command.put("dy_m", dyM);
        return Map.of("request_id", send(command));
    }

    /** REST-36. Unknown track ids are refused by the simulator (INVALID), which owns the list. */
    public Map<String, Object> selectTrack(String trackId) {
        if (trackId == null || trackId.isBlank()) {
            throw invalid("track_id 가 비어 있습니다.");
        }
        requireAvailable();
        Map<String, Object> command = command("track.select");
        command.put("track_id", trackId);
        return Map.of("request_id", send(command));
    }

    /** REST-37. */
    public Map<String, Object> raceStart() {
        requireAvailable();
        return Map.of("request_id", send(command("race.start")));
    }

    // ---- helpers ------------------------------------------------------------------------------------

    private SimState requireAvailable() {
        SimState state = tracker.snapshot();
        if (!state.available()) {
            throw new ApiException(HttpStatus.CONFLICT, "SIM_UNAVAILABLE",
                    "시뮬레이터가 연결되어 있지 않습니다 (최근 1초 안에 sim.pose 없음).");
        }
        return state;
    }

    private static void requireObstacle(SimState state, String obstacleId) {
        JsonNode obstacles = state.world() == null ? null : state.world().path("obstacles");
        if (obstacles != null && obstacles.isArray()) {
            for (JsonNode obstacle : obstacles) {
                if (obstacleId.equals(obstacle.path("id").asText(null))) {
                    return;
                }
            }
        }
        throw new ApiException(HttpStatus.NOT_FOUND, "OBSTACLE_NOT_FOUND", "그 장애물이 없습니다: " + obstacleId);
    }

    private static int obstacleCount(SimState state) {
        JsonNode obstacles = state.world() == null ? null : state.world().path("obstacles");
        return obstacles != null && obstacles.isArray() ? obstacles.size() : 0;
    }

    public static ApiException invalid(String message) {
        return new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", message);
    }

    private static Map<String, Object> command(String op) {
        Map<String, Object> command = new LinkedHashMap<>();
        command.put("op", op);
        command.put("request_id", UUID.randomUUID().toString());
        return command;
    }

    private synchronized String send(Map<String, Object> payload) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schema_version", SCHEMA_VERSION);
        envelope.put("message_id", UUID.randomUUID().toString());
        envelope.put("source_id", SOURCE_ID);
        envelope.put("boot_id", bootId);
        envelope.put("agent_instance_id", instanceId);
        envelope.put("type", "sim.command");
        envelope.put("seq", ++seq);
        envelope.put("sent_at", DateTimeFormatter.ISO_INSTANT.format(clock.instant().truncatedTo(ChronoUnit.MILLIS)));
        envelope.put("payload", payload);
        byte[] bytes;
        try {
            bytes = objectMapper.writeValueAsBytes(envelope);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException(exception);
        }
        SimCommandPublisher target = publisher.get();
        if (target == null || !target.publish(COMMAND_TOPIC, bytes, 1)) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "MQTT_UNAVAILABLE",
                    "MQTT 브로커에 연결되어 있지 않아 시뮬레이터 명령을 보내지 못했습니다.");
        }
        return (String) payload.get("request_id");
    }
}
