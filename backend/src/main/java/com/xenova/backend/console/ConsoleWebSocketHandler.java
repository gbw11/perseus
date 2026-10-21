package com.xenova.backend.console;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.auth.application.AccessTokenVerifier;
import com.xenova.backend.auth.application.AccessTokenVerifier.InvalidAccessTokenException;
import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.vm.application.VmService;
import com.xenova.backend.vm.domain.Vm;
import com.xenova.backend.vm.domain.VmState;
import com.xenova.backend.vm.infrastructure.Dom0ManagerProperties;
import jakarta.annotation.PreDestroy;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.BinaryMessage;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.AbstractWebSocketHandler;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;

/** User JWT stays in the first browser frame; the dom0 service token stays on the server. */
@Component
public class ConsoleWebSocketHandler extends AbstractWebSocketHandler {
    private static final int MAX_BYTES = 65536;
    private static final CloseStatus UNAUTHORIZED = new CloseStatus(4401, "authentication required");
    private static final CloseStatus UNAVAILABLE = new CloseStatus(1011, "console unavailable");
    private final ObjectMapper json;
    private final AccessTokenVerifier verifier;
    private final VmService vms;
    private final Dom0ManagerProperties properties;
    private final Clock clock;
    private final HttpClient http;
    private final Map<String, Connection> connections = new ConcurrentHashMap<>();
    private final ExecutorService workers = Executors.newVirtualThreadPerTaskExecutor();
    private final ScheduledExecutorService timers = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "ws-console-deadlines");
        t.setDaemon(true);
        return t;
    });

    public ConsoleWebSocketHandler(ObjectMapper json, AccessTokenVerifier verifier, VmService vms,
            Dom0ManagerProperties properties, Clock clock) {
        this.json = json;
        this.verifier = verifier;
        this.vms = vms;
        this.properties = properties;
        this.clock = clock;
        this.http = HttpClient.newBuilder().connectTimeout(properties.connectTimeout()).build();
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        session.setBinaryMessageSizeLimit(MAX_BYTES);
        session.setTextMessageSizeLimit(MAX_BYTES);
        Connection c = new Connection(new ConcurrentWebSocketSessionDecorator(session, 5000, 262144,
                ConcurrentWebSocketSessionDecorator.OverflowStrategy.TERMINATE));
        connections.put(session.getId(), c);
        c.deadline = timers.schedule(() -> {
            if (!c.ready) finish(c, c.authenticated ? UNAVAILABLE : UNAUTHORIZED);
        }, 10, TimeUnit.SECONDS);
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        Connection c = connections.get(session.getId());
        if (c == null) return;
        // Only the first text frame is a control message. Terminal input is binary.
        if (!c.started.compareAndSet(false, true)) {
            finish(c, new CloseStatus(1003, "terminal input must be binary"));
            return;
        }
        try {
            JsonNode frame = json.readTree(message.getPayload());
            if (frame == null || !"auth".equals(frame.path("type").asText())
                    || !frame.path("access_token").isTextual()) {
                finish(c, UNAUTHORIZED);
                return;
            }
            String token = frame.path("access_token").asText();
            workers.submit(() -> authenticateAndConnect(c, session.getUri(), token));
        } catch (IOException exception) {
            finish(c, UNAUTHORIZED);
        }
    }

    private void authenticateAndConnect(Connection c, URI requestUri, String token) {
        try {
            Instant expiry = verifier.verify(token).expiresAt();
            if (expiry == null || !clock.instant().isBefore(expiry)) {
                finish(c, UNAUTHORIZED);
                return;
            }
            c.token = token;
            c.authenticated = true;
            String[] path = requestUri.getPath().split("/");
            String id = path[path.length - 2];
            UUID uuid = UUID.fromString(id);
            if (!uuid.toString().equalsIgnoreCase(id) || uuid.equals(new UUID(0, 0))) {
                finish(c, new CloseStatus(4400, "invalid VM identifier"));
                return;
            }
            Vm vm = vms.get(uuid.toString()); // Same registered-VM access scope as the HTTP API.
            if (vm.state() != VmState.RUNNING && vm.state() != VmState.PAUSED) {
                finish(c, new CloseStatus(4409, "VM console is not available in this state"));
                return;
            }
            if (properties.serviceToken() == null || properties.serviceToken().isBlank()) {
                finish(c, UNAVAILABLE);
                return;
            }
            if (c.closed.get()) return;
            c.expiry = timers.schedule(() -> finish(c, UNAUTHORIZED),
                    Math.max(1, Duration.between(clock.instant(), expiry).toMillis()), TimeUnit.MILLISECONDS);
            URI base = URI.create(properties.baseUrl());
            String scheme = "https".equals(base.getScheme()) ? "wss" : "ws";
            URI target = new URI(scheme, null, base.getHost(), base.getPort(),
                    base.getPath().replaceAll("/$", "") + "/api/v1/vms/" + uuid + "/console", null, null);
            http.newWebSocketBuilder().connectTimeout(properties.connectTimeout())
                    .header("Authorization", "Bearer " + properties.serviceToken())
                    .buildAsync(target, new Upstream(c, uuid.toString()))
                    .whenComplete((upstream, error) -> {
                        if (error != null) finish(c, UNAVAILABLE);
                        else if (c.closed.get()) upstream.abort();
                    });
        } catch (InvalidAccessTokenException exception) {
            finish(c, UNAUTHORIZED);
        } catch (IllegalArgumentException exception) {
            finish(c, new CloseStatus(4400, "invalid VM identifier"));
        } catch (ApiException exception) {
            int status = exception.getStatus().value();
            finish(c, status == 404 ? new CloseStatus(4404, "VM not found") : UNAVAILABLE);
        } catch (Exception exception) {
            finish(c, UNAVAILABLE);
        } finally {
            // Browser disconnect can race with token verification / VM lookup.
            if (c.closed.get()) {
                c.token = null;
                if (c.expiry != null) c.expiry.cancel(false);
            }
        }
    }

    @Override
    protected void handleBinaryMessage(WebSocketSession session, BinaryMessage message) {
        Connection c = connections.get(session.getId());
        if (c == null) return;
        if (!c.ready || c.upstream == null) {
            finish(c, c.authenticated ? new CloseStatus(4409, "console is connecting") : UNAUTHORIZED);
            return;
        }
        if (message.getPayloadLength() > MAX_BYTES) {
            finish(c, CloseStatus.TOO_BIG_TO_PROCESS);
            return;
        }
        try {
            verifier.verify(c.token); // A revoked/expired login must not keep sending terminal input.
            c.upstream.sendBinary(message.getPayload().asReadOnlyBuffer(), true).get(5, TimeUnit.SECONDS);
        } catch (InvalidAccessTokenException exception) {
            finish(c, UNAUTHORIZED);
        } catch (Exception exception) {
            finish(c, UNAVAILABLE);
        }
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        Connection c = connections.get(session.getId());
        if (c != null) finish(c, CloseStatus.NORMAL);
    }

    @Override
    public void handleTransportError(WebSocketSession session, Throwable exception) {
        Connection c = connections.get(session.getId());
        if (c != null) finish(c, UNAVAILABLE);
    }

    private void finish(Connection c, CloseStatus status) {
        if (!c.closed.compareAndSet(false, true)) return;
        connections.remove(c.browser.getId(), c);
        if (c.deadline != null) c.deadline.cancel(false);
        if (c.expiry != null) c.expiry.cancel(false);
        c.token = null;
        WebSocket upstream = c.upstream;
        if (upstream != null) {
            upstream.sendClose(1000, "browser disconnected").orTimeout(2, TimeUnit.SECONDS)
                    .whenComplete((ignored, error) -> upstream.abort());
        }
        try {
            if (c.browser.isOpen()) c.browser.close(status);
        } catch (IOException | IllegalStateException ignored) {
            // No terminal content, JWT, or service credentials are logged.
        }
    }

    private final class Upstream implements WebSocket.Listener {
        private final Connection c;
        private final String vmId;

        private Upstream(Connection c, String vmId) {
            this.c = c;
            this.vmId = vmId;
        }

        @Override
        public void onOpen(WebSocket upstream) {
            c.upstream = upstream;
            if (c.closed.get()) {
                upstream.abort();
                return;
            }
            try {
                c.ready = true;
                c.browser.sendMessage(new TextMessage(json.writeValueAsString(
                        Map.of("type", "connected", "vm_id", vmId))));
                upstream.request(1);
            } catch (IOException | RuntimeException exception) {
                finish(c, UNAVAILABLE);
            }
        }

        @Override
        public CompletionStage<?> onBinary(WebSocket upstream, ByteBuffer data, boolean last) {
            if (c.closed.get()) return CompletableFuture.completedFuture(null);
            if (data.remaining() > MAX_BYTES) {
                finish(c, CloseStatus.TOO_BIG_TO_PROCESS);
                return CompletableFuture.completedFuture(null);
            }
            try {
                byte[] bytes = new byte[data.remaining()];
                data.get(bytes);
                c.browser.sendMessage(new BinaryMessage(bytes));
                upstream.request(1); // Demand one chunk only after the previous one is sent.
            } catch (IOException | RuntimeException exception) {
                finish(c, UNAVAILABLE);
            }
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public CompletionStage<?> onClose(WebSocket upstream, int code, String reason) {
            int safeCode = switch (code) {
                case 1000, 1001, 1008, 1009, 1011, 4400, 4401, 4404, 4409 -> code;
                default -> 1011;
            };
            finish(c, new CloseStatus(safeCode, "upstream console ended"));
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public void onError(WebSocket upstream, Throwable error) {
            finish(c, UNAVAILABLE);
        }
    }

    @PreDestroy
    void shutdown() {
        connections.values().forEach(c -> finish(c, CloseStatus.GOING_AWAY));
        workers.shutdownNow();
        timers.shutdownNow();
        http.shutdownNow();
    }

    private static final class Connection {
        final WebSocketSession browser;
        final AtomicBoolean started = new AtomicBoolean();
        final AtomicBoolean closed = new AtomicBoolean();
        volatile boolean authenticated;
        volatile boolean ready;
        volatile String token;
        volatile WebSocket upstream;
        volatile ScheduledFuture<?> deadline;
        volatile ScheduledFuture<?> expiry;

        Connection(WebSocketSession browser) { this.browser = browser; }
    }
}
