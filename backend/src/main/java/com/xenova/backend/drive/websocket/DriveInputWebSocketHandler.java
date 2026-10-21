package com.xenova.backend.drive.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.application.AccessTokenVerifier;
import com.xenova.backend.drive.application.ManualInputRelay;
import com.xenova.backend.drive.application.ManualInputRelay.Result;
import java.time.Clock;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.WebSocketSession;

// WS /ws/v1/drive (MQTT contract §7): input only. The console sends {"type":"input","keys":["up","left"]}
// whenever the held keys change (browser key names are mapped by the console). The relay repeats them at 20 Hz.
// Every input is answered with {"type":"input.ack","result":"SENT|RELEASED|NOT_MANUAL|NOT_CONNECTED"} so the
// console can show why the car does not move. Closing the socket releases all keys.
@Component
public class DriveInputWebSocketHandler extends AuthenticatedSocketHandler {

    private final ManualInputRelay relay;

    public DriveInputWebSocketHandler(
            ObjectMapper objectMapper, AccessTokenVerifier verifier, Clock clock, ManualInputRelay relay) {
        super(objectMapper, verifier, clock, "ws-drive-input");
        this.relay = relay;
    }

    @Override
    protected void onFrame(Client client, String type, String requestId, JsonNode frame) {
        if (!"input".equals(type)) {
            sendError(client, requestId, "VALIDATION_ERROR", "지원하지 않는 type입니다: " + type);
            return;
        }
        if (!frame.path("keys").isArray()) {
            sendError(client, requestId, "VALIDATION_ERROR", "keys 는 문자열 배열이어야 합니다.");
            return;
        }
        List<String> keys = new ArrayList<>();
        frame.path("keys").forEach(key -> keys.add(key.asText()));
        Result result = relay.update(keys);
        Map<String, Object> ack = frame("input.ack", requestId);
        ack.put("result", result.name());
        ack.put("keys", relay.held());
        send(client, ack);
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        Client client = clients.get(session.getId());
        super.afterConnectionClosed(session, status);
        if (client != null && client.authenticated) {
            relay.release();
        }
    }
}
