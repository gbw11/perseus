package com.xenova.backend.display.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.application.AccessTokenVerifier;
import com.xenova.backend.display.application.DisplayRegistry;
import com.xenova.backend.display.application.DisplayRegistry.DisplaysChanged;
import com.xenova.backend.drive.websocket.AuthenticatedSocketHandler;
import java.time.Clock;
import java.util.Map;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

// WS /ws/v1/displays (S15P31E110-129): read-only console feed. Same first-frame user JWT auth as
// /ws/v1/drive/state (4401 on failure).
//  server → {"type":"display.snapshot","items":[...GET /api/v1/displays items...]}   after auth and on every change
// Clients send no commands (only ping); monitors are driven by the dom0 display service on /ws/v1/display-service.
@Component
public class DisplayWebSocketHandler extends AuthenticatedSocketHandler {

    private final DisplayRegistry registry;

    public DisplayWebSocketHandler(
            ObjectMapper objectMapper, AccessTokenVerifier verifier, Clock clock, DisplayRegistry registry) {
        super(objectMapper, verifier, clock, "ws-displays");
        this.registry = registry;
    }

    @Override
    protected void onAuthenticated(Client client) {
        send(client, snapshot());
    }

    @Override
    protected void onFrame(Client client, String type, String requestId, JsonNode frame) {
        sendError(client, requestId, "VALIDATION_ERROR", "읽기 전용 소켓입니다. 지원하지 않는 type입니다: " + type);
    }

    @EventListener
    public void onDisplaysChanged(DisplaysChanged ignored) {
        Map<String, Object> snapshot = snapshot();
        for (Client client : clients.values()) {
            if (client.isAuthenticated()) {
                send(client, snapshot);
            }
        }
    }

    // Always the current list, so a late event can never push an older picture.
    private Map<String, Object> snapshot() {
        Map<String, Object> frame = frame("display.snapshot", null);
        frame.put("items", registry.list());
        return frame;
    }
}
