package com.xenova.backend.display;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.event.domain.VmEvent;
import com.xenova.backend.event.domain.VmEventRepository;
import com.xenova.backend.vm.domain.CreateVmCommand;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Predicate;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.socket.CloseStatus;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;

// S15P31E110-129: REST-23/24 and WS /ws/v1/displays. The registry is shared by every test in this context, so
// each test reads the current revision first instead of assuming a starting value.
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class DisplayRouteIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"display-user","password":"password123!"}
            """;

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate restTemplate;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private VmRecordRepository vmRecordRepository;

    @Autowired
    private VmEventRepository eventRepository;

    @Test
    void listsTwoDisplays() throws Exception {
        ResponseEntity<String> response = get("/api/v1/displays", login());
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.OK);
        JsonNode body = objectMapper.readTree(response.getBody());
        assertThat(body.path("stale").asBoolean(true)).isFalse();
        assertThat(body.path("observed_at").isTextual()).isTrue();
        assertThat(body.path("items")).hasSize(2);
        JsonNode first = body.path("items").get(0);
        assertThat(first.path("display_id").asText()).isEqualTo("display-0");
        assertThat(first.path("label").asText()).isEqualTo("모니터 1");
        assertThat(first.path("supported_views").toString()).isEqualTo("[\"split\",\"os-face\"]");
        assertThat(first.path("revision").isIntegralNumber()).isTrue();
        assertThat(first.has("desired_route")).isTrue();
        assertThat(first.has("observed_route")).isTrue();
        assertThat(first.has("last_error")).isTrue();
        assertThat(body.path("items").get(1).path("label").asText()).isEqualTo("모니터 2");
    }

    @Test
    void requiresLogin() {
        assertThat(restTemplate.getForEntity("/api/v1/displays", String.class).getStatusCode())
                .isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    @Test
    void changesRouteAndRejectsBadRequests() throws Exception {
        String token = login();
        String vmId = UUID.randomUUID().toString();
        vmRecordRepository.save(new VmRecord(vmId,
                new CreateVmCommand("display-vm-" + vmId.substring(0, 8), "debian-13-arm64", "1.0.0", 1, 256),
                Instant.now()));
        long revision = revision(token, "display-1");

        ResponseEntity<String> changed = put("display-1", token,
                "{\"vm_id\":\"%s\",\"view\":\"os-face\",\"expected_revision\":%d}".formatted(vmId, revision));
        assertThat(changed.getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        JsonNode display = objectMapper.readTree(changed.getBody());
        assertThat(display.path("revision").asLong()).isEqualTo(revision + 1);
        assertThat(display.path("desired_route").path("vm_id").asText()).isEqualTo(vmId);
        assertThat(display.path("desired_route").path("view").asText()).isEqualTo("os-face");
        assertThat(display.path("desired_route").path("request_id").isTextual()).isTrue();

        VmEvent event = eventRepository.findAll().stream()
                .filter(e -> e.getType() == EventType.DISPLAY_ROUTE_CHANGED && vmId.equals(e.getVmId()))
                .findFirst().orElseThrow();
        assertThat(event.getPayload()).containsEntry("display_id", "display-1").containsEntry("requested_by", "display-user");

        // The same expected_revision again: someone else already moved the display.
        assertError(put("display-1", token, "{\"view\":\"split\",\"expected_revision\":%d}".formatted(revision)),
                HttpStatus.CONFLICT, "DISPLAY_REVISION_CONFLICT");
        long now = revision + 1;
        assertError(put("display-9", token, "{\"view\":\"split\",\"expected_revision\":1}"),
                HttpStatus.NOT_FOUND, "DISPLAY_NOT_FOUND");
        assertError(put("display-1", token, "{\"view\":\"grid\",\"expected_revision\":%d}".formatted(now)),
                HttpStatus.UNPROCESSABLE_ENTITY, "UNSUPPORTED_VIEW");
        assertError(put("display-1", token, "{\"view\":\"os-face\",\"expected_revision\":%d}".formatted(now)),
                HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR");
        assertError(put("display-1", token,
                        "{\"vm_id\":\"%s\",\"view\":\"split\",\"expected_revision\":%d}".formatted(vmId, now)),
                HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR");
        assertError(put("display-1", token,
                        "{\"vm_id\":\"%s\",\"view\":\"os-face\",\"expected_revision\":%d}".formatted(UUID.randomUUID(), now)),
                HttpStatus.NOT_FOUND, "VM_NOT_FOUND");
        assertError(put("display-1", token, "{\"view\":\"split\"}"),
                HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR");
        assertError(put("display-1", token, "{\"view\":\"split\",\"expected_revision\":\"x\"}"),
                HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR");
        assertThat(revision(token, "display-1")).isEqualTo(now);
    }

    @Test
    void monitorFollowsRouteAndConfirmsIt() throws Exception {
        String token = login();
        Socket console = Socket.open(port, token, objectMapper);
        assertThat(console.next().path("type").asText()).isEqualTo("display.snapshot");

        Socket monitor = Socket.open(port, token, objectMapper);
        monitor.next(); // snapshot after auth
        monitor.send("{\"type\":\"display.hello\",\"display_id\":\"display-0\"}");
        JsonNode hello = item(monitor.until(s -> item(s, "display-0").path("connected").asBoolean()), "display-0");
        assertThat(hello.path("status").asText()).isEqualTo("SWITCHING");
        console.until(s -> item(s, "display-0").path("connected").asBoolean());

        long revision = hello.path("revision").asLong();
        assertThat(put("display-0", token, "{\"view\":\"split\",\"expected_revision\":%d}".formatted(revision))
                .getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        JsonNode pushed = item(monitor.until(s -> item(s, "display-0").path("revision").asLong() == revision + 1),
                "display-0");
        assertThat(pushed.path("status").asText()).isEqualTo("SWITCHING");

        monitor.send("{\"type\":\"route.applied\",\"display_id\":\"display-0\",\"revision\":%d}".formatted(revision + 1));
        JsonNode active = item(console.until(s -> "ACTIVE".equals(item(s, "display-0").path("status").asText())),
                "display-0");
        assertThat(active.path("observed_route").path("view").asText()).isEqualTo("split");
        assertThat(active.path("observed_route").path("verification_level").asText()).isEqualTo("browser_ready");
        assertThat(active.path("observed_route").path("applied_at").isTextual()).isTrue();

        // A stale revision is refused with an error frame and changes nothing.
        monitor.send("{\"type\":\"route.applied\",\"display_id\":\"display-0\",\"revision\":%d}".formatted(revision));
        assertThat(monitor.until(s -> "error".equals(s.path("type").asText())).path("code").asText())
                .isEqualTo("DISPLAY_REVISION_CONFLICT");
        monitor.send("{\"type\":\"display.hello\",\"display_id\":\"display-7\"}");
        assertThat(monitor.until(s -> "error".equals(s.path("type").asText())).path("code").asText())
                .isEqualTo("DISPLAY_NOT_FOUND");

        monitor.session.close();
        JsonNode gone = item(console.until(s -> !item(s, "display-0").path("connected").asBoolean()), "display-0");
        assertThat(gone.path("status").asText()).isEqualTo("DISCONNECTED");
        assertThat(gone.path("observed_route").isNull()).isTrue();
        console.session.close();
    }

    @Test
    void socketWithoutAuthIsClosedWith4401() throws Exception {
        Socket socket = new Socket(objectMapper);
        socket.session = new StandardWebSocketClient()
                .execute(socket, "ws://127.0.0.1:" + port + "/ws/v1/displays").get(5, TimeUnit.SECONDS);
        socket.send("{\"type\":\"display.hello\",\"display_id\":\"display-0\"}");
        CloseStatus status = socket.closed.poll(5, TimeUnit.SECONDS);
        assertThat(status).isNotNull();
        assertThat(status.getCode()).isEqualTo(4401);
    }

    // ---- helpers ------------------------------------------------------------------------------------

    private static JsonNode item(JsonNode snapshot, String displayId) {
        for (JsonNode item : snapshot.path("items")) {
            if (displayId.equals(item.path("display_id").asText())) {
                return item;
            }
        }
        return objectMapper().createObjectNode();
    }

    private static ObjectMapper objectMapper() {
        return new ObjectMapper();
    }

    private long revision(String token, String displayId) throws Exception {
        return item(objectMapper.readTree(get("/api/v1/displays", token).getBody()), displayId).path("revision").asLong();
    }

    private void assertError(ResponseEntity<String> response, HttpStatus status, String code) throws Exception {
        assertThat(response.getStatusCode()).isEqualTo(status);
        assertThat(objectMapper.readTree(response.getBody()).path("code").asText()).isEqualTo(code);
    }

    private ResponseEntity<String> get(String path, String token) {
        return restTemplate.exchange(path, HttpMethod.GET, new HttpEntity<>(bearer(token)), String.class);
    }

    private ResponseEntity<String> put(String displayId, String token, String body) {
        HttpHeaders headers = bearer(token);
        headers.setContentType(MediaType.APPLICATION_JSON);
        return restTemplate.exchange("/api/v1/displays/" + displayId + "/route", HttpMethod.PUT,
                new HttpEntity<>(body, headers), String.class);
    }

    private String login() throws Exception {
        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.APPLICATION_JSON);
        restTemplate.postForEntity("/api/v1/auth/signup", new HttpEntity<>(CREDENTIALS, headers), String.class);
        String body = restTemplate.postForEntity("/api/v1/auth/login", new HttpEntity<>(CREDENTIALS, headers),
                String.class).getBody();
        return objectMapper.readTree(body).get("access_token").asText();
    }

    private static HttpHeaders bearer(String token) {
        HttpHeaders headers = new HttpHeaders();
        headers.setBearerAuth(token);
        return headers;
    }

    private static final class Socket extends TextWebSocketHandler {
        private final BlockingQueue<String> messages = new LinkedBlockingQueue<>();
        private final BlockingQueue<CloseStatus> closed = new LinkedBlockingQueue<>();
        private final ObjectMapper objectMapper;
        private WebSocketSession session;

        private Socket(ObjectMapper objectMapper) {
            this.objectMapper = objectMapper;
        }

        static Socket open(int port, String token, ObjectMapper objectMapper) throws Exception {
            Socket socket = new Socket(objectMapper);
            socket.session = new StandardWebSocketClient()
                    .execute(socket, "ws://127.0.0.1:" + port + "/ws/v1/displays").get(5, TimeUnit.SECONDS);
            socket.send("{\"type\":\"auth\",\"access_token\":\"" + token + "\"}");
            assertThat(socket.next().path("type").asText()).isEqualTo("authenticated");
            return socket;
        }

        @Override
        protected void handleTextMessage(WebSocketSession session, TextMessage message) {
            messages.add(message.getPayload());
        }

        @Override
        public void afterConnectionClosed(WebSocketSession session, CloseStatus status) {
            closed.add(status);
        }

        void send(String text) throws Exception {
            session.sendMessage(new TextMessage(text));
        }

        JsonNode next() throws Exception {
            String message = messages.poll(5, TimeUnit.SECONDS);
            assertThat(message).as("expected a WebSocket message").isNotNull();
            return objectMapper.readTree(message);
        }

        /** Skips frames until one matches (snapshots from other tests' changes may arrive in between). */
        JsonNode until(Predicate<JsonNode> condition) throws Exception {
            while (true) {
                JsonNode frame = next();
                if (condition.test(frame)) {
                    return frame;
                }
            }
        }
    }
}
