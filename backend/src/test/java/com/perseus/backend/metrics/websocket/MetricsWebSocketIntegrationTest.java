package com.perseus.backend.metrics.websocket;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.perseus.backend.agent.application.AgentDataService;
import com.perseus.backend.agent.domain.MetricSample;
import com.perseus.backend.agent.domain.MetricValue;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.UUID;
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
class MetricsWebSocketIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"ws-metrics-user","password":"password123!"}
            """;

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate restTemplate;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private AgentDataService agentDataService;

    @Test
    void subscribedClientReceivesNewSamplesOfItsVmOnly() throws Exception {
        Client client = connect();
        client.send("{\"type\":\"auth\",\"access_token\":\"" + login() + "\"}");
        assertThat(client.next().path("type").asText()).isEqualTo("authenticated");

        client.send("""
                {"type":"subscribe","request_id":"r-1","vm_ids":["vm-a"],"names":["cpu_percent_allocated"]}
                """);
        JsonNode subscribed = client.next();
        assertThat(subscribed.path("type").asText()).isEqualTo("subscribed");
        assertThat(subscribed.path("request_id").asText()).isEqualTo("r-1");
        assertThat(subscribed.path("interval_ms").asLong()).isEqualTo(2000);

        record("vm-b");
        String sampleId = record("vm-a");
        JsonNode pushed = client.next();
        assertThat(pushed.path("type").asText()).isEqualTo("metrics.sample");
        assertThat(pushed.path("seq").asLong()).isEqualTo(1);
        assertThat(pushed.path("stream_id").asText()).isEqualTo(subscribed.path("stream_id").asText());
        assertThat(pushed.path("sample_id").asText()).isEqualTo(sampleId);
        assertThat(pushed.path("payload").path("vm_id").asText()).isEqualTo("vm-a");
        assertThat(pushed.path("payload").path("values")).hasSize(1);
        assertThat(pushed.path("payload").path("values").get(0).path("name").asText())
                .isEqualTo("cpu_percent_allocated");

        client.send("{\"type\":\"ping\",\"request_id\":\"r-2\"}");
        assertThat(client.next().path("type").asText()).isEqualTo("pong");
        client.session.close();
    }

    @Test
    void invalidTokenClosesTheConnection() throws Exception {
        Client client = connect();
        client.send("{\"type\":\"auth\",\"access_token\":\"not-a-jwt\"}");
        assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4401);
    }

    @Test
    void subscribingBeforeAuthenticationClosesTheConnection() throws Exception {
        Client client = connect();
        client.send("{\"type\":\"subscribe\",\"request_id\":\"r-1\",\"vm_ids\":[\"vm-a\"]}");
        assertThat(client.closed.get(5, TimeUnit.SECONDS).getCode()).isEqualTo(4401);
    }

    private String record(String vmId) {
        String sampleId = UUID.randomUUID().toString();
        Instant now = Instant.now();
        agentDataService.recordMetrics(new MetricSample(sampleId, vmId, "boot-1", "guest_agent", now, now, 2000,
                List.of(new MetricValue("cpu_percent_allocated", 42.0, "percent", Map.of(), null),
                        new MetricValue("memory_used_bytes", 1.0e8, "bytes", Map.of(), null))), List.of());
        return sampleId;
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
                .execute(client, "ws://127.0.0.1:" + port + "/ws/v1/metrics")
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
