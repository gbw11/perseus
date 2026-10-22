package com.xenova.backend.display;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.application.AccessTokenVerifier;
import jakarta.annotation.PreDestroy;
import jakarta.annotation.PostConstruct;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.AbstractWebSocketHandler;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;

@Component
public class DisplayStateWebSocket extends AbstractWebSocketHandler {
    private static final CloseStatus UNAUTHORIZED = new CloseStatus(4401, "authentication required");
    private final ObjectMapper json;
    private final AccessTokenVerifier verifier;
    private final Clock clock;
    private final DisplayFeedClient feed;
    private final Map<String, Client> clients = new ConcurrentHashMap<>();
    private final ScheduledExecutorService timers = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "display-auth-deadlines"); t.setDaemon(true); return t;
    });
    private final ScheduledExecutorService publisher = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "display-state-publisher"); t.setDaemon(true); return t;
    });

    public DisplayStateWebSocket(ObjectMapper json, AccessTokenVerifier verifier, Clock clock, DisplayFeedClient feed) {
        this.json = json; this.verifier = verifier; this.clock = clock; this.feed = feed;
    }

    @PostConstruct
    public void start() {
        // A slow collector must not block drive/events tasks on Spring's shared scheduler.
        publisher.scheduleWithFixedDelay(this::publish, 1, 1, TimeUnit.SECONDS);
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        session.setTextMessageSizeLimit(16384);
        Client c = new Client(new ConcurrentWebSocketSessionDecorator(session, 3000, 131072,
                ConcurrentWebSocketSessionDecorator.OverflowStrategy.TERMINATE));
        clients.put(session.getId(), c);
        c.deadline = timers.schedule(() -> { if (c.expiry == null) close(c, UNAUTHORIZED); }, 10, TimeUnit.SECONDS);
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        Client c = clients.get(session.getId());
        if (c == null) return;
        synchronized (c) {
            if (c.expiry != null) { close(c, new CloseStatus(1008, "read-only state stream")); return; }
            try {
                JsonNode frame = json.readTree(message.getPayload());
                if (frame == null || !"auth".equals(frame.path("type").asText())
                        || !frame.path("access_token").isTextual()) throw new IllegalArgumentException();
                Instant expiry = verifier.verify(frame.path("access_token").asText()).expiresAt();
                if (expiry == null || !clock.instant().isBefore(expiry)) throw new IllegalArgumentException();
                if (clients.get(session.getId()) != c || !c.session.isOpen()) return;
                c.expiry = expiry;
                c.deadline.cancel(false);
                c.deadline = timers.schedule(() -> close(c, UNAUTHORIZED),
                        Math.max(1, Duration.between(clock.instant(), expiry).toMillis()), TimeUnit.MILLISECONDS);
                send(c, Map.of("type", "authenticated"));
            } catch (Exception exception) { close(c, UNAUTHORIZED); }
        }
    }

    @Override
    protected void handleBinaryMessage(WebSocketSession session, BinaryMessage message) {
        Client c = clients.get(session.getId());
        if (c != null) close(c, c.expiry == null ? UNAUTHORIZED : new CloseStatus(1008, "read-only state stream"));
    }

    public void publish() {
        var ready = clients.values().stream().filter(c -> c.expiry != null).toList();
        if (ready.isEmpty()) return; // No service request before user authentication.
        Object body;
        try { body = Map.of("type", "display.state", "state", feed.snapshot()); }
        catch (Exception exception) {
            body = Map.of("type", "display.unavailable", "code", "DISPLAY_FEED_UNAVAILABLE");
        }
        for (Client c : ready) {
            if (!clock.instant().isBefore(c.expiry)) close(c, UNAUTHORIZED);
            else send(c, body);
        }
    }

    private void send(Client c, Object body) {
        try { c.session.sendMessage(new TextMessage(json.writeValueAsString(body))); }
        catch (Exception exception) { close(c, CloseStatus.SERVER_ERROR); }
    }

    private void close(Client c, CloseStatus status) {
        synchronized (c) {
            clients.remove(c.session.getId());
            if (c.deadline != null) c.deadline.cancel(false);
            try { c.session.close(status); } catch (Exception ignored) { }
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        Client c = clients.remove(session.getId());
        if (c != null && c.deadline != null) c.deadline.cancel(false);
    }

    @PreDestroy
    public void stop() {
        clients.values().forEach(c -> close(c, CloseStatus.GOING_AWAY));
        timers.shutdownNow();
        publisher.shutdownNow();
    }

    private static class Client {
        final WebSocketSession session;
        volatile Instant expiry;
        volatile ScheduledFuture<?> deadline;
        Client(WebSocketSession session) { this.session = session; }
    }
}
