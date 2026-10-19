package com.xenova.backend.event.websocket;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.event.application.EventRecorder;
import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.event.domain.VmEvent;
import java.util.Map;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class EventsWebSocketIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"ws-events-user","password":"password123!"}
            """;

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate restTemplate;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private EventRecorder eventRecorder;

    @Test
    void subscribedClientReceivesMatchingEventsOnly() throws Exception {
        Client client = authenticatedClient();
        client.send("""
                {"type":"subscribe","request_id":"r-1","vm_ids":["vm-ws-a"],"event_types":["vm.state.changed"]}
                """);
        JsonNode subscribed = client.next();
        assertThat(subscribed.path("type").asText()).isEqualTo("subscribed");
        assertThat(subscribed.path("request_id").asText()).isEqualTo("r-1");
        assertThat(subscribed.path("replayed_count").asInt()).isZero();

        record("vm-ws-b", EventType.VM_STATE_CHANGED);
        record("vm-ws-a", EventType.VM_DELETED);
        VmEvent expected = record("vm-ws-a", EventType.VM_STATE_CHANGED);

        JsonNode pushed = client.next();
        assertThat(pushed.path("schema_version").asText()).isEqualTo("1.0");
        assertThat(pushed.path("type").asText()).isEqualTo("event");
        assertThat(pushed.path("seq").asLong()).isEqualTo(1);
        assertThat(pushed.path("stream_id").asText()).isEqualTo(subscribed.path("stream_id").asText());
        assertThat(pushed.path("payload").path("event_id").asText()).isEqualTo(expected.getEventId());
        assertThat(pushed.path("payload").path("type").asText()).isEqualTo("vm.state.changed");
        assertThat(pushed.path("payload").path("payload").path("to").asText()).isEqualTo("running");

        client.send("{\"type\":\"ping\",\"request_id\":\"r-2\"}");
        assertThat(client.next().path("type").asText()).isEqualTo("pong");
        client.session.close();
    }

    @Test
    void subscribeWithAfterEventIdReplaysTheMissedEvents() throws Exception {
        VmEvent lastSeen = record("vm-ws-c", EventType.VM_CREATED);
        VmEvent missed1 = record("vm-ws-c", EventType.VM_STATE_CHANGED);
        VmEvent missed2 = record("vm-ws-c", EventType.VM_DELETED);

        Client client = authenticatedClient();
        client.send("""
                {"type":"subscribe","request_id":"r-1","vm_ids":["vm-ws-c"],"after_event_id":"%s"}
                """.formatted(lastSeen.getEventId()));

        assertThat(client.next().path("replayed_count").asInt()).isEqualTo(2);
        assertThat(client.next().path("payload").path("event_id").asText()).isEqualTo(missed1.getEventId());
        JsonNode second = client.next();
        assertThat(second.path("payload").path("event_id").asText()).isEqualTo(missed2.getEventId());
        assertThat(second.path("seq").asLong()).isEqualTo(2);
        client.session.close();
    }

    @Test
    void unknownAfterEventIdAsksForResync() throws Exception {
        Client client = authenticatedClient();
        client.send("""
                {"type":"subscribe","request_id":"r-1","after_event_id":"gone"}
                """);

        assertThat(client.next().path("type").asText()).isEqualTo("subscribed");
        JsonNode resync = client.next();
        assertThat(resync.path("type").asText()).isEqualTo("resync_required");
        assertThat(resync.path("reason").asText()).isEqualTo("EVENT_CURSOR_EXPIRED");
        client.session.close();
    }

    @Test
    void unsupportedEventTypeIsRejected() throws Exception {
        Client client = authenticatedClient();
        client.send("""
                {"type":"subscribe","request_id":"r-1","event_types":["vm.exploded"]}
                """);

        JsonNode error = client.next();
        assertThat(error.path("type").asText()).isEqualTo("error");
        assertThat(error.path("code").asText()).isEqualTo("EVENT_TYPE_UNSUPPORTED");
        client.session.close();
    }

    @Test
    void invalidTokenClosesTheConnection() throws Exception {
        Client client = connect();
        client.send("{\"type\":\"auth\",\"access_token\":\"not-a-jwt\"}");
        assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4401);
    }

    private VmEvent record(String vmId, EventType type) {
        return eventRecorder.record(type, EventSeverity.INFO, vmId, null, Map.of("to", "running"));
    }

    private Client authenticatedClient() throws Exception {
        Client client = connect();
        client.send("{\"type\":\"auth\",\"access_token\":\"" + login() + "\"}");
        assertThat(client.next().path("type").asText()).isEqualTo("authenticated");
        return client;
    }

    private String login() throws Exception {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        restTemplate.postForEntity("/api/v1/auth/signup", new HttpEntity<>(CREDENTIALS, headers), String.class);
        String body = restTemplate.postForEntity("/api/v1/auth/login", new HttpEntity<>(CREDENTIALS, headers),
                String.class).getBody();
        return objectMapper.readTree(body).get("access_token").asText();
    }

    private Client connect() throws Exception {
        Client client = new Client();
        client.session = new StandardWebSocketClient()
                .execute(client, "ws://127.0.0.1:" + port + "/ws/v1/events")
                .get(5, TimeUnit.SECONDS);
        return client;
    }

    private final class Client extends TextWebSocketHandler {

        private final BlockingQueue<String> messages = new LinkedBlockingQueue<>();
        private final CompletableFuture<CloseStatus> closed = new CompletableFuture<>();
        private WebSocketSession session;

        @Override
        protected void handleTextMessage(WebSocketSession session, TextMessage message) {
            messages.add(message.getPayload());
        }

        @Override
        public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
            closed.complete(status);
        }

        void send(String text) throws Exception {
            session.sendMessage(new TextMessage(text));
        }

        JsonNode next() throws Exception {
            String message = messages.poll(5, TimeUnit.SECONDS);
            assertThat(message).as("expected a WebSocket message").isNotNull();
            return objectMapper.readTree(message);
        }
    }
}
