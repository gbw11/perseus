package com.xenova.backend.event.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.application.AccessTokenVerifier;
import com.xenova.backend.auth.application.AccessTokenVerifier.InvalidAccessTokenException;
import com.xenova.backend.auth.application.AccessTokenVerifier.VerifiedToken;
import com.xenova.backend.event.api.EventResponse;
import com.xenova.backend.event.application.EventQueryService;
import com.xenova.backend.event.application.EventRecorded;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.event.domain.VmEvent;
import java.io.IOException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;
import org.springframework.web.socket.handler.TextWebSocketHandler;

// WS-02 /ws/v1/events: pushes each stored event to subscribed clients.
// Same handshake as WS-01: {"type":"auth","access_token":...} first, then subscribe/ping frames.
// Unlike metrics, events are never skipped: a client that cannot keep up is told to resync and closed.
@Component
public class EventsWebSocketHandler extends TextWebSocketHandler {

    static final CloseStatus UNAUTHORIZED = new CloseStatus(4401, "unauthorized");
    static final CloseStatus SLOW_CONSUMER = new CloseStatus(1008, "SLOW_CONSUMER");
    static final int MAX_FRAME_BYTES = 64 * 1024;
    static final int MAX_REPLAY = 500;
    private static final Duration AUTH_TIMEOUT = Duration.ofSeconds(10);
    private static final int SEND_TIME_LIMIT_MS = 5000;
    private static final int BUFFER_LIMIT_BYTES = 1024 * 1024;
    private static final int SLOW_CONSUMER_BYTES = 512 * 1024;

    private static final Logger log = LoggerFactory.getLogger(EventsWebSocketHandler.class);

    private final ObjectMapper objectMapper;
    private final AccessTokenVerifier accessTokenVerifier;
    private final EventQueryService eventQueryService;
    private final Clock clock;
    private final Map<String, Subscriber> subscribers = new ConcurrentHashMap<>();
    private final ScheduledExecutorService authTimeouts = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "ws-events-auth-timeout");
        thread.setDaemon(true);
        return thread;
    });

    public EventsWebSocketHandler(
            ObjectMapper objectMapper,
            AccessTokenVerifier accessTokenVerifier,
            EventQueryService eventQueryService,
            Clock clock) {
        this.objectMapper = objectMapper;
        this.accessTokenVerifier = accessTokenVerifier;
        this.eventQueryService = eventQueryService;
        this.clock = clock;
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        WebSocketSession safe = new ConcurrentWebSocketSessionDecorator(
                session, SEND_TIME_LIMIT_MS, BUFFER_LIMIT_BYTES,
                ConcurrentWebSocketSessionDecorator.OverflowStrategy.TERMINATE);
        Subscriber subscriber = new Subscriber(safe);
        subscribers.put(session.getId(), subscriber);
        authTimeouts.schedule(() -> {
            if (!subscriber.authenticated) {
                close(subscriber, UNAUTHORIZED);
            }
        }, AUTH_TIMEOUT.toMillis(), TimeUnit.MILLISECONDS);
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        Subscriber subscriber = subscribers.get(session.getId());
        if (subscriber == null) {
            return;
        }
        if (message.getPayloadLength() > MAX_FRAME_BYTES) {
            close(subscriber, CloseStatus.TOO_BIG_TO_PROCESS);
            return;
        }
        JsonNode frame;
        try {
            frame = objectMapper.readTree(message.getPayload());
        } catch (IOException exception) {
            sendError(subscriber, null, "VALIDATION_ERROR", "JSON 형식이 아닙니다.");
            return;
        }
        String type = frame.path("type").asText();
        String requestId = frame.path("request_id").isTextual() ? frame.path("request_id").asText() : null;

        if (!subscriber.authenticated) {
            if ("auth".equals(type)) {
                authenticate(subscriber, frame.path("access_token").asText(null));
            } else {
                close(subscriber, UNAUTHORIZED);
            }
            return;
        }
        switch (type) {
            case "subscribe" -> subscribe(subscriber, requestId, frame);
            case "ping" -> send(subscriber, frame("pong", requestId));
            default -> sendError(subscriber, requestId, "VALIDATION_ERROR", "지원하지 않는 type입니다: " + type);
        }
    }

    private void authenticate(Subscriber subscriber, String token) {
        try {
            VerifiedToken verified = accessTokenVerifier.verify(token);
            subscriber.expiresAt = verified.expiresAt();
            subscriber.authenticated = true;
            send(subscriber, frame("authenticated", null));
        } catch (InvalidAccessTokenException exception) {
            close(subscriber, UNAUTHORIZED);
        }
    }

    // A new subscribe replaces the previous one. Missing vm_ids / event_types mean everything.
    // With after_event_id the stored events after it are replayed before live events continue.
    private void subscribe(Subscriber subscriber, String requestId, JsonNode frame) {
        if (requestId == null) {
            sendError(subscriber, null, "VALIDATION_ERROR", "request_id가 필요합니다.");
            return;
        }
        Set<EventType> types = new HashSet<>();
        for (String value : strings(frame.path("event_types"))) {
            Optional<EventType> type = EventType.fromValue(value);
            if (type.isEmpty()) {
                sendError(subscriber, requestId, "EVENT_TYPE_UNSUPPORTED", "지원하지 않는 이벤트 종류입니다: " + value);
                return;
            }
            types.add(type.get());
        }
        Set<String> vmIds = Set.copyOf(strings(frame.path("vm_ids")));
        String afterEventId = frame.path("after_event_id").isTextual() ? frame.path("after_event_id").asText() : null;

        // Holding the lock keeps live events from overtaking the replay; one may arrive twice,
        // which clients drop by event_id.
        synchronized (subscriber) {
            subscriber.vmIds = vmIds;
            subscriber.types = Set.copyOf(types);
            subscriber.subscribed = true;
            List<VmEvent> replay = List.of();
            boolean resync = false;
            if (afterEventId != null) {
                Optional<List<VmEvent>> stored = eventQueryService.replayAfter(afterEventId, vmIds, types, MAX_REPLAY);
                resync = stored.isEmpty();
                replay = stored.orElse(List.of());
            }
            Map<String, Object> reply = frame("subscribed", requestId);
            reply.put("stream_id", subscriber.streamId);
            reply.put("replayed_count", replay.size());
            send(subscriber, reply);
            if (resync) {
                sendResync(subscriber, "EVENT_CURSOR_EXPIRED");
            }
            replay.forEach(event -> sendEvent(subscriber, event));
        }
    }

    @TransactionalEventListener(fallbackExecution = true)
    public void onEventRecorded(EventRecorded recorded) {
        VmEvent event = recorded.event();
        Instant now = clock.instant();
        for (Subscriber subscriber : subscribers.values()) {
            if (!subscriber.authenticated || !subscriber.subscribed || !subscriber.accepts(event)) {
                continue;
            }
            if (subscriber.expiresAt != null && !now.isBefore(subscriber.expiresAt)) {
                close(subscriber, UNAUTHORIZED);
                continue;
            }
            synchronized (subscriber) {
                ConcurrentWebSocketSessionDecorator session = (ConcurrentWebSocketSessionDecorator) subscriber.session;
                if (session.getBufferSize() > SLOW_CONSUMER_BYTES) {
                    sendResync(subscriber, "SLOW_CONSUMER");
                    close(subscriber, SLOW_CONSUMER);
                    continue;
                }
                sendEvent(subscriber, event);
            }
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        subscribers.remove(session.getId());
    }

    private void sendEvent(Subscriber subscriber, VmEvent event) {
        Map<String, Object> frame = new LinkedHashMap<>();
        frame.put("schema_version", "1.0");
        frame.put("type", "event");
        frame.put("stream_id", subscriber.streamId);
        frame.put("seq", ++subscriber.seq);
        frame.put("payload", EventResponse.of(event));
        send(subscriber, frame);
    }

    private void sendResync(Subscriber subscriber, String reason) {
        Map<String, Object> resync = frame("resync_required", null);
        resync.put("stream_id", subscriber.streamId);
        resync.put("reason", reason);
        send(subscriber, resync);
    }

    private void sendError(Subscriber subscriber, String requestId, String code, String message) {
        Map<String, Object> error = frame("error", requestId);
        error.put("code", code);
        error.put("message", message);
        error.put("retryable", false);
        send(subscriber, error);
    }

    private void send(Subscriber subscriber, Map<String, Object> frame) {
        try {
            subscriber.session.sendMessage(new TextMessage(objectMapper.writeValueAsString(frame)));
        } catch (IOException | IllegalStateException exception) {
            log.debug("WebSocket send failed for {}: {}", subscriber.session.getId(), exception.getMessage());
        }
    }

    private void close(Subscriber subscriber, CloseStatus status) {
        subscribers.remove(subscriber.session.getId());
        try {
            subscriber.session.close(status);
        } catch (IOException exception) {
            log.debug("WebSocket close failed: {}", exception.getMessage());
        }
    }

    private static Map<String, Object> frame(String type, String requestId) {
        Map<String, Object> frame = new LinkedHashMap<>();
        frame.put("type", type);
        if (requestId != null) {
            frame.put("request_id", requestId);
        }
        return frame;
    }

    private static List<String> strings(JsonNode array) {
        List<String> values = new ArrayList<>();
        if (array.isArray()) {
            array.forEach(node -> {
                if (node.isTextual() && !node.asText().isBlank()) {
                    values.add(node.asText());
                }
            });
        }
        return values;
    }

    private static final class Subscriber {

        private final WebSocketSession session;
        private final String streamId = UUID.randomUUID().toString();
        // Only touched while holding the subscriber lock.
        private long seq;
        private volatile boolean authenticated;
        private volatile boolean subscribed;
        private volatile Instant expiresAt;
        private volatile Set<String> vmIds = Set.of();
        private volatile Set<EventType> types = Set.of();

        private Subscriber(WebSocketSession session) {
            this.session = session;
        }

        private boolean accepts(VmEvent event) {
            return (vmIds.isEmpty() || vmIds.contains(event.getVmId()))
                    && (types.isEmpty() || types.contains(event.getType()));
        }
    }
}
