package com.perseus.backend.metrics.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.perseus.backend.agent.application.MetricSampleRecorded;
import com.perseus.backend.agent.domain.MetricSample;
import com.perseus.backend.agent.domain.MetricValue;
import com.perseus.backend.auth.application.AccessTokenVerifier;
import com.perseus.backend.auth.application.AccessTokenVerifier.InvalidAccessTokenException;
import com.perseus.backend.auth.application.AccessTokenVerifier.VerifiedToken;
import com.perseus.backend.common.error.ApiException;
import com.perseus.backend.metrics.api.MetricSampleResponse;
import com.perseus.backend.metrics.application.MetricFilter;
import java.io.IOException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionalEventListener;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;
import org.springframework.web.socket.handler.TextWebSocketHandler;

// WS-01 /ws/v1/metrics: pushes newly stored metric samples to subscribed clients.
// Protocol: {"type":"auth","access_token":...} first, then subscribe/unsubscribe/ping frames.
@Component
public class MetricsWebSocketHandler extends TextWebSocketHandler {

    static final CloseStatus UNAUTHORIZED = new CloseStatus(4401, "unauthorized");
    static final int MAX_FRAME_BYTES = 64 * 1024;
    static final long INTERVAL_MS = 2000;
    private static final Duration AUTH_TIMEOUT = Duration.ofSeconds(10);
    private static final int SEND_TIME_LIMIT_MS = 5000;

    private static final Logger log = LoggerFactory.getLogger(MetricsWebSocketHandler.class);

    private final ObjectMapper objectMapper;
    private final AccessTokenVerifier accessTokenVerifier;
    private final Clock clock;
    private final Map<String, Subscriber> subscribers = new ConcurrentHashMap<>();
    private final ScheduledExecutorService authTimeouts = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "ws-metrics-auth-timeout");
        thread.setDaemon(true);
        return thread;
    });

    public MetricsWebSocketHandler(ObjectMapper objectMapper, AccessTokenVerifier accessTokenVerifier, Clock clock) {
        this.objectMapper = objectMapper;
        this.accessTokenVerifier = accessTokenVerifier;
        this.clock = clock;
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        // The decorator serialises concurrent sends; a client that cannot keep up gets no unbounded queue.
        WebSocketSession safe = new ConcurrentWebSocketSessionDecorator(
                session, SEND_TIME_LIMIT_MS, MAX_FRAME_BYTES, ConcurrentWebSocketSessionDecorator.OverflowStrategy.DROP);
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
            case "unsubscribe" -> unsubscribe(subscriber, requestId, frame);
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

    // A new subscribe replaces the previous subscription of this connection.
    private void subscribe(Subscriber subscriber, String requestId, JsonNode frame) {
        List<String> vmIds = strings(frame.path("vm_ids"));
        if (requestId == null || vmIds.isEmpty()) {
            sendError(subscriber, requestId, "VALIDATION_ERROR", "request_id와 vm_ids가 필요합니다.");
            return;
        }
        MetricFilter filter;
        try {
            filter = MetricFilter.of(
                    frame.has("sources") ? strings(frame.path("sources")) : null,
                    frame.has("names") ? strings(frame.path("names")) : null);
        } catch (ApiException exception) {
            sendError(subscriber, requestId, exception.getCode(), exception.getMessage());
            return;
        }
        subscriber.vmIds = Set.copyOf(vmIds);
        subscriber.filter = filter;
        Map<String, Object> reply = frame("subscribed", requestId);
        reply.put("stream_id", subscriber.streamId);
        reply.put("vm_ids", vmIds);
        reply.put("interval_ms", INTERVAL_MS);
        send(subscriber, reply);
    }

    private void unsubscribe(Subscriber subscriber, String requestId, JsonNode frame) {
        List<String> removed = strings(frame.path("vm_ids"));
        Set<String> remaining = new HashSet<>(subscriber.vmIds);
        remaining.removeAll(removed);
        subscriber.vmIds = Set.copyOf(remaining);
        Map<String, Object> reply = frame("unsubscribed", requestId);
        reply.put("vm_ids", removed);
        send(subscriber, reply);
    }

    @TransactionalEventListener(fallbackExecution = true)
    public void onMetricSampleRecorded(MetricSampleRecorded event) {
        MetricSample sample = event.sample();
        Instant now = clock.instant();
        for (Subscriber subscriber : subscribers.values()) {
            if (!subscriber.authenticated || !subscriber.vmIds.contains(sample.getVmId())
                    || !subscriber.filter.acceptsSource(sample)) {
                continue;
            }
            if (subscriber.expiresAt != null && !now.isBefore(subscriber.expiresAt)) {
                close(subscriber, UNAUTHORIZED);
                continue;
            }
            List<MetricValue> values = subscriber.filter.select(sample);
            if (!values.isEmpty()) {
                push(subscriber, sample, values, now);
            }
        }
    }

    // While a slow client still has unsent frames, new samples are skipped instead of queued;
    // once it catches up it is told to refill the gap from REST-12.
    private void push(Subscriber subscriber, MetricSample sample, List<MetricValue> values, Instant now) {
        ConcurrentWebSocketSessionDecorator session = (ConcurrentWebSocketSessionDecorator) subscriber.session;
        if (session.getBufferSize() > 0) {
            subscriber.lagging = true;
            return;
        }
        if (subscriber.lagging) {
            subscriber.lagging = false;
            Map<String, Object> resync = frame("resync_required", null);
            resync.put("stream_id", subscriber.streamId);
            send(subscriber, resync);
        }
        Map<String, Object> frame = new LinkedHashMap<>();
        frame.put("schema_version", "1.0");
        frame.put("type", "metrics.sample");
        frame.put("stream_id", subscriber.streamId);
        frame.put("seq", subscriber.seq.incrementAndGet());
        frame.put("sample_id", sample.getSampleId());
        frame.put("emitted_at", now);
        frame.put("payload", MetricSampleResponse.of(sample, values));
        send(subscriber, frame);
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        subscribers.remove(session.getId());
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
        private final AtomicLong seq = new AtomicLong();
        private volatile boolean authenticated;
        private volatile Instant expiresAt;
        private volatile Set<String> vmIds = Set.of();
        private volatile MetricFilter filter = new MetricFilter(Set.of(), Set.of());
        private volatile boolean lagging;

        private Subscriber(WebSocketSession session) {
            this.session = session;
        }
    }
}
