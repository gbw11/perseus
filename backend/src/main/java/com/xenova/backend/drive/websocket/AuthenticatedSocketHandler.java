package com.xenova.backend.drive.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.application.AccessTokenVerifier;
import com.xenova.backend.auth.application.AccessTokenVerifier.InvalidAccessTokenException;
import java.io.IOException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;
import org.springframework.web.socket.handler.TextWebSocketHandler;

// Same handshake as WS-01/WS-02: the first frame is {"type":"auth","access_token":...}; anything else, or
// nothing within 10 s, closes with 4401. After that the subclass handles frames; "ping" answers "pong".
abstract class AuthenticatedSocketHandler extends TextWebSocketHandler {

    static final CloseStatus UNAUTHORIZED = new CloseStatus(4401, "unauthorized");
    static final int MAX_FRAME_BYTES = 16 * 1024;
    private static final Duration AUTH_TIMEOUT = Duration.ofSeconds(10);
    private static final Logger log = LoggerFactory.getLogger(AuthenticatedSocketHandler.class);

    protected final ObjectMapper objectMapper;
    private final AccessTokenVerifier verifier;
    protected final Clock clock;
    protected final Map<String, Client> clients = new ConcurrentHashMap<>();
    private final ScheduledExecutorService authTimeouts;

    AuthenticatedSocketHandler(ObjectMapper objectMapper, AccessTokenVerifier verifier, Clock clock, String name) {
        this.objectMapper = objectMapper;
        this.verifier = verifier;
        this.clock = clock;
        this.authTimeouts = Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, name + "-auth-timeout");
            thread.setDaemon(true);
            return thread;
        });
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        Client client = new Client(new ConcurrentWebSocketSessionDecorator(
                session, 5000, 256 * 1024, ConcurrentWebSocketSessionDecorator.OverflowStrategy.DROP));
        clients.put(session.getId(), client);
        authTimeouts.schedule(() -> {
            if (!client.authenticated) {
                close(client, UNAUTHORIZED);
            }
        }, AUTH_TIMEOUT.toMillis(), TimeUnit.MILLISECONDS);
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        Client client = clients.get(session.getId());
        if (client == null) {
            return;
        }
        if (message.getPayloadLength() > MAX_FRAME_BYTES) {
            close(client, CloseStatus.TOO_BIG_TO_PROCESS);
            return;
        }
        JsonNode frame;
        try {
            frame = objectMapper.readTree(message.getPayload());
        } catch (IOException exception) {
            sendError(client, null, "VALIDATION_ERROR", "JSON 형식이 아닙니다.");
            return;
        }
        String type = frame.path("type").asText();
        String requestId = frame.path("request_id").isTextual() ? frame.path("request_id").asText() : null;
        if (!client.authenticated) {
            authenticate(client, "auth".equals(type) ? frame.path("access_token").asText(null) : null);
            return;
        }
        if (client.expiresAt != null && clock.instant().isAfter(client.expiresAt)) {
            close(client, UNAUTHORIZED);
            return;
        }
        if ("ping".equals(type)) {
            send(client, frame("pong", requestId));
            return;
        }
        onFrame(client, type, requestId, frame);
    }

    protected abstract void onFrame(Client client, String type, String requestId, JsonNode frame);

    protected void onAuthenticated(Client client) {
    }

    private void authenticate(Client client, String token) {
        try {
            client.expiresAt = verifier.verify(token).expiresAt();
            client.authenticated = true;
            send(client, frame("authenticated", null));
            onAuthenticated(client);
        } catch (InvalidAccessTokenException | IllegalArgumentException exception) {
            close(client, UNAUTHORIZED);
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        clients.remove(session.getId());
    }

    protected Map<String, Object> frame(String type, String requestId) {
        Map<String, Object> frame = new LinkedHashMap<>();
        frame.put("type", type);
        if (requestId != null) {
            frame.put("request_id", requestId);
        }
        return frame;
    }

    protected void sendError(Client client, String requestId, String code, String message) {
        Map<String, Object> frame = frame("error", requestId);
        frame.put("code", code);
        frame.put("message", message);
        send(client, frame);
    }

    protected void send(Client client, Object body) {
        try {
            client.session.sendMessage(new TextMessage(objectMapper.writeValueAsString(body)));
        } catch (IOException | IllegalStateException exception) {
            log.debug("Drive socket send failed: {}", exception.getMessage());
        }
    }

    protected void close(Client client, CloseStatus status) {
        clients.remove(client.session.getId());
        try {
            client.session.close(status);
        } catch (IOException exception) {
            log.debug("Drive socket close failed: {}", exception.getMessage());
        }
    }

    static final class Client {
        final WebSocketSession session;
        volatile boolean authenticated;
        volatile Instant expiresAt;
        volatile long sentVersion = -1;
        volatile long sentAtMillis;

        Client(WebSocketSession session) {
            this.session = session;
        }
    }
}
