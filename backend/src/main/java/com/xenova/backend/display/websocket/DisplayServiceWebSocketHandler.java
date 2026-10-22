package com.xenova.backend.display.websocket;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.display.application.DisplayRegistry;
import com.xenova.backend.display.application.DisplayRegistry.DisplayLink;
import com.xenova.backend.display.application.DisplayRegistry.RouteChanged;
import com.xenova.backend.display.application.DisplayRegistry.RouteRejected;
import com.xenova.backend.display.application.DisplayRegistry.RouteSet;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.handler.ConcurrentWebSocketSessionDecorator;
import org.springframework.web.socket.handler.TextWebSocketHandler;

// WS /ws/v1/display-service (S15P31E110-129): the dom0 display service (platform/services/display) takes screen
// routes from here and drives the kiosk browsers. No user JWT: the first frame carries a shared secret.
//  service → {"type":"auth","service_token":"..."}   wrong, missing or not configured → 4401; ok → {"type":"authenticated"}
//  service → {"type":"service.hello","displays":[{"display_id":"display-0","connected":true}, ...]}
//  service → {"type":"display.status","display_id":"display-1","connected":true}
//  service → {"type":"route.applied","display_id":"display-0","revision":3,"request_id":"..."}
//  service → {"type":"route.failed","display_id":"display-0","revision":3,"request_id":"...","reason":"..."}
//  server  → {"type":"route.set","display_id","revision","request_id","view","vm_id","app_id","screen_path"}
//            for every display right after hello, then after every PUT.
//  server  → {"type":"error","code","message"}   code UNAUTHORIZED / VALIDATION_ERROR / DISPLAY_NOT_FOUND
// An older revision in route.applied / route.failed is dropped silently. Contract: docs/contracts/display-route-v1.md.
// One service at a time: a newly authenticated socket replaces the old one, which is closed with 4409.
@Component
public class DisplayServiceWebSocketHandler extends TextWebSocketHandler {

    public static final CloseStatus UNAUTHORIZED = new CloseStatus(4401, "unauthorized");
    public static final CloseStatus REPLACED = new CloseStatus(4409, "replaced by a newer display service");
    private static final int MAX_FRAME_BYTES = 16 * 1024;
    private static final Duration AUTH_TIMEOUT = Duration.ofSeconds(10);
    private static final Logger log = LoggerFactory.getLogger(DisplayServiceWebSocketHandler.class);

    private final ObjectMapper objectMapper;
    private final DisplayRegistry registry;
    private final byte[] serviceToken;
    private final Map<String, Peer> peers = new ConcurrentHashMap<>();
    private final ScheduledExecutorService authTimeouts;
    // The one authenticated service. Written under this, which also keeps route.set frames in revision order.
    private volatile Peer active;

    public DisplayServiceWebSocketHandler(
            ObjectMapper objectMapper, DisplayRegistry registry,
            @Value("${xenova.display.service-token:}") String serviceToken) {
        this.objectMapper = objectMapper;
        this.registry = registry;
        this.serviceToken = serviceToken == null ? new byte[0] : serviceToken.getBytes(StandardCharsets.UTF_8);
        this.authTimeouts = Executors.newSingleThreadScheduledExecutor(runnable -> {
            Thread thread = new Thread(runnable, "ws-display-service-auth-timeout");
            thread.setDaemon(true);
            return thread;
        });
        if (this.serviceToken.length == 0) {
            log.warn("xenova.display.service-token is empty; WS /ws/v1/display-service refuses every connection");
        }
    }

    @Override
    public void afterConnectionEstablished(WebSocketSession session) {
        Peer peer = new Peer(new ConcurrentWebSocketSessionDecorator(
                session, 5000, 256 * 1024, ConcurrentWebSocketSessionDecorator.OverflowStrategy.DROP));
        peers.put(session.getId(), peer);
        if (serviceToken.length == 0) {
            sendError(peer, null, "UNAUTHORIZED", "표시 서비스 토큰이 설정되지 않았습니다.");
            close(peer, UNAUTHORIZED);
            return;
        }
        authTimeouts.schedule(() -> {
            if (!peer.authenticated) {
                sendError(peer, null, "UNAUTHORIZED", "10초 안에 인증하지 않았습니다.");
                close(peer, UNAUTHORIZED);
            }
        }, AUTH_TIMEOUT.toMillis(), TimeUnit.MILLISECONDS);
    }

    @Override
    protected void handleTextMessage(WebSocketSession session, TextMessage message) {
        Peer peer = peers.get(session.getId());
        if (peer == null) {
            return;
        }
        if (message.getPayloadLength() > MAX_FRAME_BYTES) {
            close(peer, CloseStatus.TOO_BIG_TO_PROCESS);
            return;
        }
        JsonNode frame;
        try {
            frame = objectMapper.readTree(message.getPayload());
        } catch (IOException exception) {
            sendError(peer, null, "VALIDATION_ERROR", "JSON 형식이 아닙니다.");
            return;
        }
        String type = frame.path("type").asText();
        String requestId = frame.path("request_id").isTextual() ? frame.path("request_id").asText() : null;
        if (!peer.authenticated) {
            authenticate(peer, "auth".equals(type) ? frame.path("service_token").asText(null) : null);
            return;
        }
        if (peer != active) {
            return; // replaced; its 4409 close is on the way
        }
        if ("ping".equals(type)) {
            send(peer, frame("pong", requestId));
            return;
        }
        switch (type) {
            case "service.hello" -> hello(peer, frame);
            case "display.status" -> displayStatus(peer, requestId, frame);
            case "route.applied", "route.failed" -> routeResult(peer, type, requestId, frame);
            default -> sendError(peer, requestId, "VALIDATION_ERROR", "지원하지 않는 type입니다: " + type);
        }
    }

    private void authenticate(Peer peer, String token) {
        // An unset secret must never match (two empty arrays would compare equal).
        if (serviceToken.length == 0 || token == null
                || !MessageDigest.isEqual(serviceToken, token.getBytes(StandardCharsets.UTF_8))) {
            sendError(peer, null, "UNAUTHORIZED", "표시 서비스 토큰이 올바르지 않습니다.");
            close(peer, UNAUTHORIZED);
            return;
        }
        Peer previous;
        synchronized (this) {
            peer.authenticated = true;
            previous = active;
            active = peer;
        }
        send(peer, frame("authenticated", null));
        if (previous != null) {
            close(previous, REPLACED);
            // The new service has not said hello yet, so nothing it reports is known.
            registry.serviceDisconnected();
        }
    }

    private void hello(Peer peer, JsonNode frame) {
        JsonNode displays = frame.path("displays");
        if (!displays.isArray()) {
            sendError(peer, null, "VALIDATION_ERROR", "displays 는 배열이어야 합니다.");
            return;
        }
        List<DisplayLink> links = new ArrayList<>();
        for (JsonNode display : displays) {
            if (!display.path("display_id").isTextual() || !display.path("connected").isBoolean()) {
                sendError(peer, null, "VALIDATION_ERROR", "displays 항목에는 display_id 와 connected 가 필요합니다.");
                return;
            }
            links.add(new DisplayLink(display.path("display_id").asText(), display.path("connected").asBoolean()));
        }
        synchronized (this) {
            if (peer != active) {
                return;
            }
            peer.helloed = true;
            registry.serviceConnected(links);
            for (String displayId : registry.displayIds()) {
                pushRoute(peer, displayId);
            }
        }
    }

    private void displayStatus(Peer peer, String requestId, JsonNode frame) {
        if (!helloed(peer, requestId)) {
            return;
        }
        if (!frame.path("connected").isBoolean()) {
            sendError(peer, requestId, "VALIDATION_ERROR", "connected 는 boolean 이어야 합니다.");
            return;
        }
        try {
            registry.displayStatus(displayId(frame), frame.path("connected").asBoolean());
        } catch (RouteRejected rejected) {
            sendError(peer, requestId, rejected.code(), rejected.getMessage());
        }
    }

    private void routeResult(Peer peer, String type, String requestId, JsonNode frame) {
        if (!helloed(peer, requestId)) {
            return;
        }
        JsonNode revision = frame.path("revision");
        Long value = revision.isIntegralNumber() ? revision.asLong() : null;
        try {
            if ("route.applied".equals(type)) {
                registry.applied(displayId(frame), value, requestId);
            } else {
                registry.failed(displayId(frame), value, requestId,
                        frame.path("reason").isTextual() ? frame.path("reason").asText() : null);
            }
        } catch (RouteRejected rejected) {
            sendError(peer, requestId, rejected.code(), rejected.getMessage());
        }
    }

    // A PUT changed desired_route: send route.set if a service is attached (otherwise it gets it on hello).
    @EventListener
    public void onRouteChanged(RouteChanged changed) {
        synchronized (this) {
            if (active != null && active.helloed) {
                pushRoute(active, changed.displayId());
            }
        }
    }

    // Caller holds this. Reads the current desired_route, so a late call never sends an older revision.
    private void pushRoute(Peer peer, String displayId) {
        registry.routeSet(displayId).ifPresent(route -> {
            send(peer, routeSetFrame(route));
            registry.routeSent(displayId, route.revision());
        });
    }

    private Map<String, Object> routeSetFrame(RouteSet route) {
        Map<String, Object> frame = frame("route.set", null);
        frame.put("display_id", route.displayId());
        frame.put("revision", route.revision());
        frame.put("request_id", route.requestId());
        frame.put("view", route.view());
        frame.put("vm_id", route.vmId());
        frame.put("app_id", route.appId());
        frame.put("screen_path", route.screenPath());
        return frame;
    }

    private boolean helloed(Peer peer, String requestId) {
        if (peer.helloed) {
            return true;
        }
        sendError(peer, requestId, "VALIDATION_ERROR", "service.hello 를 먼저 보내야 합니다.");
        return false;
    }

    private static String displayId(JsonNode frame) {
        return frame.path("display_id").isTextual() ? frame.path("display_id").asText() : null;
    }

    @Override
    public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
        Peer peer = peers.remove(session.getId());
        boolean wasActive;
        synchronized (this) {
            wasActive = peer != null && peer == active;
            if (wasActive) {
                active = null;
            }
        }
        if (wasActive) {
            registry.serviceDisconnected();
        }
    }

    private Map<String, Object> frame(String type, String requestId) {
        Map<String, Object> frame = new LinkedHashMap<>();
        frame.put("type", type);
        if (requestId != null) {
            frame.put("request_id", requestId);
        }
        return frame;
    }

    private void sendError(Peer peer, String requestId, String code, String message) {
        Map<String, Object> frame = frame("error", requestId);
        frame.put("code", code);
        frame.put("message", message);
        send(peer, frame);
    }

    private void send(Peer peer, Object body) {
        try {
            peer.session.sendMessage(new TextMessage(objectMapper.writeValueAsString(body)));
        } catch (IOException | IllegalStateException exception) {
            log.debug("Display service socket send failed: {}", exception.getMessage());
        }
    }

    private void close(Peer peer, CloseStatus status) {
        try {
            peer.session.close(status);
        } catch (IOException exception) {
            log.debug("Display service socket close failed: {}", exception.getMessage());
        }
    }

    private static final class Peer {
        private final WebSocketSession session;
        private volatile boolean authenticated;
        private volatile boolean helloed;

        private Peer(WebSocketSession session) {
            this.session = session;
        }
    }
}
