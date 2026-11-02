package com.xenova.backend.sim.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.sim.application.SimCommandService;
import com.xenova.backend.sim.application.SimState;
import com.xenova.backend.sim.application.SimStateTracker;
import java.io.IOException;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

// Simulator console API (MQTT contract §12.2). REST-30 state (S15P31E110-270); REST-31~37 controls (S15P31E110-271)
// answer 202 when the sim.command is handed to the broker — the result arrives on WS-06 /ws/v1/sim.
// Bodies are read as JSON trees so a wrong type or missing field is a 422 VALIDATION_ERROR, not a 500.
@RestController
public class SimController {

    private final SimStateTracker tracker;
    private final SimCommandService commands;
    private final ObjectMapper objectMapper;

    public SimController(SimStateTracker tracker, SimCommandService commands, ObjectMapper objectMapper) {
        this.tracker = tracker;
        this.commands = commands;
        this.objectMapper = objectMapper;
    }

    @GetMapping("/api/v1/sim/state")
    public SimState state() {
        return tracker.snapshot();
    }

    @PostMapping("/api/v1/sim/obstacles")
    public ResponseEntity<Map<String, Object>> addObstacle(@RequestBody(required = false) String body) {
        JsonNode json = read(body);
        JsonNode kind = json.path("kind");
        if (!kind.isMissingNode() && !kind.isNull() && !kind.isTextual()) {
            throw SimCommandService.invalid("kind 는 문자열입니다.");
        }
        return accepted(commands.addObstacle(kind.isTextual() ? kind.asText() : null, number(json, "x"), number(json, "y")));
    }

    @PutMapping("/api/v1/sim/obstacles/{obstacleId}")
    public ResponseEntity<Map<String, Object>> moveObstacle(@PathVariable String obstacleId,
            @RequestBody(required = false) String body) {
        JsonNode json = read(body);
        return accepted(commands.moveObstacle(obstacleId, number(json, "x"), number(json, "y")));
    }

    @DeleteMapping("/api/v1/sim/obstacles/{obstacleId}")
    public ResponseEntity<Map<String, Object>> removeObstacle(@PathVariable String obstacleId) {
        return accepted(commands.removeObstacle(obstacleId));
    }

    @PostMapping("/api/v1/sim/estop")
    public ResponseEntity<Map<String, Object>> estop(@RequestBody(required = false) String body) {
        JsonNode engaged = read(body).path("engaged");
        if (!engaged.isBoolean()) {
            throw SimCommandService.invalid("engaged 는 true/false 입니다.");
        }
        return accepted(commands.estop(engaged.asBoolean()));
    }

    @PostMapping("/api/v1/sim/push")
    public ResponseEntity<Map<String, Object>> push(@RequestBody(required = false) String body) {
        return accepted(commands.push(number(read(body), "dy_m")));
    }

    @PostMapping("/api/v1/sim/track")
    public ResponseEntity<Map<String, Object>> selectTrack(@RequestBody(required = false) String body) {
        JsonNode trackId = read(body).path("track_id");
        return accepted(commands.selectTrack(trackId.isTextual() ? trackId.asText() : null));
    }

    @PostMapping("/api/v1/sim/race/start")
    public ResponseEntity<Map<String, Object>> raceStart() {
        return accepted(commands.raceStart());
    }

    private static ResponseEntity<Map<String, Object>> accepted(Map<String, Object> body) {
        return ResponseEntity.status(HttpStatus.ACCEPTED).body(body);
    }

    private JsonNode read(String body) {
        if (body == null || body.isBlank()) {
            return objectMapper.createObjectNode();
        }
        try {
            JsonNode json = objectMapper.readTree(body);
            if (json != null && json.isObject()) {
                return json;
            }
        } catch (IOException exception) {
            // fall through
        }
        throw SimCommandService.invalid("요청 본문이 JSON 객체가 아닙니다.");
    }

    private static double number(JsonNode json, String field) {
        JsonNode value = json.path(field);
        if (!value.isNumber() || !Double.isFinite(value.asDouble())) {
            throw SimCommandService.invalid(field + " 는 숫자입니다.");
        }
        return value.asDouble();
    }
}
