package com.xenova.backend.sim.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.application.AccessTokenVerifier;
import com.xenova.backend.drive.websocket.AuthenticatedSocketHandler;
import com.xenova.backend.sim.application.SimState;
import com.xenova.backend.sim.application.SimStateTracker;
import java.time.Clock;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

// WS-06 /ws/v1/sim (MQTT contract §12.2, S15P31E110-270). Server → console only:
//   {"type":"sim.snapshot", available, pose, world} every 100 ms (latest pose only) and once more right away when
//   sim.world changes; {"type":"sim.command.result", ...payload} as soon as MQTT-18 arrives.
// Same first-frame auth as the drive sockets. Monitor screen 1's third card switches to the simulator on `available`.
@Component
public class SimWebSocketHandler extends AuthenticatedSocketHandler {

    static final long PUSH_INTERVAL_MS = 100;

    private final SimStateTracker tracker;

    public SimWebSocketHandler(ObjectMapper objectMapper, AccessTokenVerifier verifier, Clock clock, SimStateTracker tracker) {
        super(objectMapper, verifier, clock, "ws-sim");
        this.tracker = tracker;
        tracker.onWorldChange(this::pushSnapshots);
        tracker.onResult(this::pushResult);
    }

    @Override
    protected void onAuthenticated(Client client) {
        send(client, snapshotFrame(tracker.snapshot()));
    }

    @Override
    protected void onFrame(Client client, String type, String requestId, JsonNode frame) {
        sendError(client, requestId, "VALIDATION_ERROR", "지원하지 않는 type입니다: " + type);
    }

    @Scheduled(fixedRate = PUSH_INTERVAL_MS)
    public void pushSnapshots() {
        if (clients.isEmpty()) {
            return;
        }
        Map<String, Object> frame = snapshotFrame(tracker.snapshot());
        for (Client client : clients.values()) {
            if (client.isAuthenticated()) {
                send(client, frame);
            }
        }
    }

    private void pushResult(JsonNode result) {
        Map<String, Object> frame = new LinkedHashMap<>();
        frame.put("type", "sim.command.result");
        result.fields().forEachRemaining(field -> frame.put(field.getKey(), field.getValue()));
        for (Client client : clients.values()) {
            if (client.isAuthenticated()) {
                send(client, frame);
            }
        }
    }

    private static Map<String, Object> snapshotFrame(SimState state) {
        Map<String, Object> frame = new LinkedHashMap<>();
        frame.put("type", "sim.snapshot");
        frame.put("available", state.available());
        frame.put("pose", state.pose());
        frame.put("world", state.world());
        return frame;
    }
}
