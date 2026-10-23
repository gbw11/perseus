package com.xenova.backend.drive.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.application.AccessTokenVerifier;
import com.xenova.backend.drive.application.DriveState;
import com.xenova.backend.drive.application.DriveStateTracker;
import java.time.Clock;
import java.util.Map;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

// WS /ws/v1/drive/state: pushes {"type":"drive.state","state":{...GET /api/v1/drive/state...}}.
// At most 10 per second and only when something changed, plus one per second so "*_ago_ms" keeps counting
// even when every publisher has stopped (that silence is exactly what the dashboard must show).
@Component
public class DriveStateWebSocketHandler extends AuthenticatedSocketHandler {

    static final long PUSH_INTERVAL_MS = 100;
    static final long IDLE_PUSH_MS = 1000;

    private final DriveStateTracker tracker;

    public DriveStateWebSocketHandler(
            ObjectMapper objectMapper, AccessTokenVerifier verifier, Clock clock, DriveStateTracker tracker) {
        super(objectMapper, verifier, clock, "ws-drive-state");
        this.tracker = tracker;
    }

    @Override
    protected void onAuthenticated(Client client) {
        push(client, tracker.snapshot());
    }

    @Override
    protected void onFrame(Client client, String type, String requestId, JsonNode frame) {
        sendError(client, requestId, "VALIDATION_ERROR", "지원하지 않는 type입니다: " + type);
    }

    @Scheduled(fixedRate = PUSH_INTERVAL_MS)
    public void pushChanges() {
        if (clients.isEmpty()) {
            return;
        }
        long version = tracker.version();
        long now = System.currentTimeMillis();
        DriveState state = null;
        for (Client client : clients.values()) {
            if (!client.authenticated) {
                continue;
            }
            if (client.sentVersion != version || now - client.sentAtMillis >= IDLE_PUSH_MS) {
                if (state == null) {
                    state = tracker.snapshot();
                }
                push(client, state);
            }
        }
    }

    private void push(Client client, DriveState state) {
        client.sentVersion = state.version();
        client.sentAtMillis = System.currentTimeMillis();
        send(client, Map.of("type", "drive.state", "state", state));
    }
}
