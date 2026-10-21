package com.xenova.backend.display.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.application.AccessTokenVerifier;
import com.xenova.backend.display.application.DisplayRegistry;
import com.xenova.backend.display.application.DisplayRegistry.DisplaysChanged;
import com.xenova.backend.display.application.DisplayRegistry.RouteRejected;
import com.xenova.backend.drive.websocket.AuthenticatedSocketHandler;
import java.time.Clock;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.WebSocketSession;

// WS /ws/v1/displays (S15P31E110-129). Same first-frame auth as /ws/v1/drive/state (4401 on failure).
//  client → {"type":"display.hello","display_id":"display-0"}   a monitor browser following that display
//  client → {"type":"route.applied","display_id":"display-0","revision":3}   the monitor now shows revision 3
//  server → {"type":"display.snapshot","items":[...GET /api/v1/displays items...]}   after auth and on every change
// A console only needs to authenticate; it never says hello.
@Component
public class DisplayWebSocketHandler extends AuthenticatedSocketHandler {

    private final DisplayRegistry registry;
    // session id → display the socket said hello for (one display per socket; a second hello moves it).
    private final Map<String, String> monitors = new ConcurrentHashMap<>();

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
        String displayId = frame.path("display_id").isTextual() ? frame.path("display_id").asText() : null;
        switch (type) {
            case "display.hello" -> hello(client, requestId, displayId);
            case "route.applied" -> {
                JsonNode revision = frame.path("revision");
                try {
                    registry.applied(displayId, revision.isIntegralNumber() ? revision.asLong() : null);
                } catch (RouteRejected rejected) {
                    sendError(client, requestId, rejected.code(), rejected.getMessage());
                }
            }
            default -> sendError(client, requestId, "VALIDATION_ERROR", "지원하지 않는 type입니다: " + type);
        }
    }

    private void hello(Client client, String requestId, String displayId) {
        if (!registry.exists(displayId)) {
            sendError(client, requestId, "DISPLAY_NOT_FOUND", "존재하지 않는 디스플레이입니다.");
            return;
        }
        String previous = monitors.put(client.id(), displayId);
        if (displayId.equals(previous)) {
            send(client, snapshot());
            return;
        }
        if (previous != null) {
            registry.disconnected(previous);
        }
        registry.connected(displayId); // pushes a snapshot to everyone, this monitor included
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        super.afterConnectionClosed(session, status);
        String displayId = monitors.remove(session.getId());
        if (displayId != null) {
            registry.disconnected(displayId);
        }
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
