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
import com.xenova.backend.vm.domain.VmRole;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.function.Predicate;
import org.junit.jupiter.api.AfterEach;
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

// S15P31E110-129: REST-23/24, the console feed WS /ws/v1/displays and the dom0 display service socket
// WS /ws/v1/display-service. The registry is shared by every test in this context, so each test reads the current
// revision first instead of assuming a starting value, and closes its sockets afterwards.
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class DisplayRouteIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"display-user","password":"password123!"}
            """;
    private static final String SERVICE_TOKEN = "test-display-service-token";
    private static final String CONSOLE_PATH = "/ws/v1/displays";
    private static final String SERVICE_PATH = "/ws/v1/display-service";

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

    private final List<Socket> sockets = new ArrayList<>();

    @AfterEach
    void closeSockets() throws Exception {
        for (Socket socket : sockets) {
            if (socket.session.isOpen()) {
                socket.session.close();
            }
        }
    }

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
        String vmId = vm(VmRole.AUTONOMOUS);
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
        // A VM without a role has no dedicated screen to open.
        assertError(put("display-1", token,
                        "{\"vm_id\":\"%s\",\"view\":\"os-face\",\"expected_revision\":%d}".formatted(vm(null), now)),
                HttpStatus.UNPROCESSABLE_ENTITY, "VM_ROLE_UNKNOWN");
        assertError(put("display-1", token, "{\"view\":\"split\"}"),
                HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR");
        assertError(put("display-1", token, "{\"view\":\"split\",\"expected_revision\":\"x\"}"),
                HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR");
        assertThat(revision(token, "display-1")).isEqualTo(now);
    }

    @Test
    void displayServiceReceivesRoutesAndReportsThem() throws Exception {
        String token = login();
        String autonomousVm = vm(VmRole.AUTONOMOUS);
        String boardVm = vm(VmRole.MESSAGE_BOARD);
        Socket console = console(token);
        console.next(); // snapshot after auth

        // hello → one route.set per display with its current desired_route.
        Socket service = service();
        service.send("""
                {"type":"service.hello","displays":[{"display_id":"display-0","connected":true},
                 {"display_id":"display-1","connected":false}]}""");
        JsonNode first = service.next();
        JsonNode second = service.next();
        assertThat(first.path("type").asText()).isEqualTo("route.set");
        assertThat(first.path("display_id").asText()).isEqualTo("display-0");
        assertThat(first.path("revision").asLong()).isEqualTo(revision(token, "display-0"));
        assertThat(first.path("request_id").isTextual()).isTrue();
        assertThat(second.path("type").asText()).isEqualTo("route.set");
        assertThat(second.path("display_id").asText()).isEqualTo("display-1");
        JsonNode switching = item(console.until(s -> "SWITCHING".equals(item(s, "display-0").path("status").asText())),
                "display-0");
        assertThat(switching.path("connected").asBoolean()).isTrue();
        assertThat(item(console.until(s -> true), "display-1").path("status").asText()).isEqualTo("DISCONNECTED");

        // PUT → route.set with the role's screen.
        long revision = revision(token, "display-0");
        assertThat(put("display-0", token, "{\"vm_id\":\"%s\",\"view\":\"os-face\",\"expected_revision\":%d}"
                .formatted(autonomousVm, revision)).getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        JsonNode set = service.until(f -> "route.set".equals(f.path("type").asText())
                && f.path("revision").asLong() == revision + 1);
        assertThat(set.path("display_id").asText()).isEqualTo("display-0");
        assertThat(set.path("view").asText()).isEqualTo("os-face");
        assertThat(set.path("vm_id").asText()).isEqualTo(autonomousVm);
        assertThat(set.path("app_id").asText()).isEqualTo("autonomous");
        assertThat(set.path("screen_path").asText()).isEqualTo("/detail/autonomous");

        // route.applied → ACTIVE with observed_route.
        service.send("{\"type\":\"route.applied\",\"display_id\":\"display-0\",\"revision\":%d,\"request_id\":\"%s\"}"
                .formatted(revision + 1, set.path("request_id").asText()));
        JsonNode active = item(console.until(s -> "ACTIVE".equals(item(s, "display-0").path("status").asText())),
                "display-0");
        assertThat(active.path("observed_route").path("view").asText()).isEqualTo("os-face");
        assertThat(active.path("observed_route").path("vm_id").asText()).isEqualTo(autonomousVm);
        assertThat(active.path("observed_route").path("verification_level").asText()).isEqualTo("kiosk_loaded");
        assertThat(active.path("observed_route").path("applied_at").isTextual()).isTrue();

        // An older revision is dropped without an answer; a future revision, another request_id or an unknown
        // display is an error frame. Each error is the very next frame, so the stale one produced nothing.
        service.send("{\"type\":\"route.failed\",\"display_id\":\"display-0\",\"revision\":%d,\"reason\":\"old\"}"
                .formatted(revision));
        service.send("{\"type\":\"route.applied\",\"display_id\":\"display-0\",\"revision\":%d}".formatted(revision + 5));
        JsonNode future = service.next();
        assertThat(future.path("code").asText()).isEqualTo("VALIDATION_ERROR");
        assertThat(future.path("message").asText()).contains("revision");
        service.send("{\"type\":\"route.applied\",\"display_id\":\"display-0\",\"revision\":%d,\"request_id\":\"other\"}"
                .formatted(revision + 1));
        JsonNode otherRequest = service.next();
        assertThat(otherRequest.path("code").asText()).isEqualTo("VALIDATION_ERROR");
        assertThat(otherRequest.path("message").asText()).contains("request_id");
        service.send("{\"type\":\"display.status\",\"display_id\":\"display-7\",\"connected\":true}");
        assertThat(service.next().path("code").asText()).isEqualTo("DISPLAY_NOT_FOUND");
        assertThat(item(objectMapper.readTree(get("/api/v1/displays", token).getBody()), "display-0")
                .path("status").asText()).isEqualTo("ACTIVE");

        // split → /split, then route.failed → ERROR with last_error; observed_route keeps the last good screen.
        assertThat(put("display-0", token, "{\"view\":\"split\",\"expected_revision\":%d}".formatted(revision + 1))
                .getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        JsonNode split = service.until(f -> "route.set".equals(f.path("type").asText())
                && f.path("revision").asLong() == revision + 2);
        assertThat(split.path("screen_path").asText()).isEqualTo("/split");
        assertThat(split.path("vm_id").isNull()).isTrue();
        assertThat(split.path("app_id").isNull()).isTrue();
        service.send("""
                {"type":"route.failed","display_id":"display-0","revision":%d,"reason":"kiosk timeout"}"""
                .formatted(revision + 2));
        JsonNode error = item(console.until(s -> "ERROR".equals(item(s, "display-0").path("status").asText())),
                "display-0");
        assertThat(error.path("last_error").asText()).isEqualTo("kiosk timeout");
        assertThat(error.path("observed_route").path("view").asText()).isEqualTo("os-face");

        // message-board → interaction screen, sent even while that monitor is unplugged. Plugging it in only
        // changes the status: the service already holds that route.set.
        long other = revision(token, "display-1");
        assertThat(put("display-1", token, "{\"vm_id\":\"%s\",\"view\":\"os-face\",\"expected_revision\":%d}"
                .formatted(boardVm, other)).getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        JsonNode board = service.until(f -> "route.set".equals(f.path("type").asText())
                && "display-1".equals(f.path("display_id").asText()));
        assertThat(board.path("revision").asLong()).isEqualTo(other + 1);
        assertThat(board.path("app_id").asText()).isEqualTo("message-board");
        assertThat(board.path("screen_path").asText()).isEqualTo("/detail/interaction");
        service.send("{\"type\":\"display.status\",\"display_id\":\"display-1\",\"connected\":true}");
        assertThat(item(console.until(s -> item(s, "display-1").path("connected").asBoolean()), "display-1")
                .path("status").asText()).isEqualTo("SWITCHING");

        // The same route again still bumps the revision and re-sends route.set (a refresh).
        assertThat(put("display-1", token, "{\"vm_id\":\"%s\",\"view\":\"os-face\",\"expected_revision\":%d}"
                .formatted(boardVm, other + 1)).getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        assertThat(service.until(f -> "route.set".equals(f.path("type").asText())
                && "display-1".equals(f.path("display_id").asText())).path("revision").asLong()).isEqualTo(other + 2);

        // The service goes away → every display DISCONNECTED and nothing observed.
        service.session.close();
        JsonNode gone = console.until(s -> "DISCONNECTED".equals(item(s, "display-0").path("status").asText())
                && "DISCONNECTED".equals(item(s, "display-1").path("status").asText()));
        assertThat(item(gone, "display-0").path("connected").asBoolean()).isFalse();
        assertThat(item(gone, "display-0").path("observed_route").isNull()).isTrue();
        assertThat(item(gone, "display-0").path("last_error").isNull()).isTrue();
    }

    @Test
    void secondServiceReplacesTheFirstWith4409() throws Exception {
        Socket old = service();
        old.send("{\"type\":\"service.hello\",\"displays\":[{\"display_id\":\"display-0\",\"connected\":true}]}");
        assertThat(old.next().path("type").asText()).isEqualTo("route.set");

        Socket replacement = service();
        CloseStatus status = old.closed.poll(5, TimeUnit.SECONDS);
        assertThat(status).isNotNull();
        assertThat(status.getCode()).isEqualTo(4409);

        replacement.send("{\"type\":\"service.hello\",\"displays\":[{\"display_id\":\"display-0\",\"connected\":true}]}");
        assertThat(replacement.next().path("type").asText()).isEqualTo("route.set");
        assertThat(replacement.session.isOpen()).isTrue();
    }

    @Test
    void serviceFramesBeforeHelloAreRefused() throws Exception {
        Socket service = service();
        service.send("{\"type\":\"route.applied\",\"display_id\":\"display-0\",\"revision\":1}");
        assertThat(service.next().path("code").asText()).isEqualTo("VALIDATION_ERROR");
    }

    @Test
    void serviceWithWrongTokenIsClosedWith4401() throws Exception {
        Socket wrong = Socket.connect(port, SERVICE_PATH, objectMapper);
        wrong.send("{\"type\":\"auth\",\"service_token\":\"nope\"}");
        assertThat(wrong.next().path("code").asText()).isEqualTo("UNAUTHORIZED");
        assertThat(wrong.closed.poll(5, TimeUnit.SECONDS).getCode()).isEqualTo(4401);

        // A user JWT is not a service token.
        Socket user = Socket.connect(port, SERVICE_PATH, objectMapper);
        user.send("{\"type\":\"auth\",\"access_token\":\"" + login() + "\"}");
        assertThat(user.closed.poll(5, TimeUnit.SECONDS).getCode()).isEqualTo(4401);
    }

    @Test
    void consoleSocketIsReadOnly() throws Exception {
        Socket console = console(login());
        assertThat(console.next().path("type").asText()).isEqualTo("display.snapshot");
        console.send("{\"type\":\"display.hello\",\"display_id\":\"display-0\"}");
        assertThat(console.until(f -> "error".equals(f.path("type").asText())).path("code").asText())
                .isEqualTo("VALIDATION_ERROR");
        console.send("{\"type\":\"route.applied\",\"display_id\":\"display-0\",\"revision\":1}");
        assertThat(console.until(f -> "error".equals(f.path("type").asText())).path("code").asText())
                .isEqualTo("VALIDATION_ERROR");
    }

    @Test
    void consoleSocketWithoutAuthIsClosedWith4401() throws Exception {
        Socket socket = Socket.connect(port, CONSOLE_PATH, objectMapper);
        socket.send("{\"type\":\"display.hello\",\"display_id\":\"display-0\"}");
        CloseStatus status = socket.closed.poll(5, TimeUnit.SECONDS);
        assertThat(status).isNotNull();
        assertThat(status.getCode()).isEqualTo(4401);
    }

    // ---- helpers ------------------------------------------------------------------------------------

    private Socket console(String token) throws Exception {
        Socket socket = track(Socket.connect(port, CONSOLE_PATH, objectMapper));
        socket.send("{\"type\":\"auth\",\"access_token\":\"" + token + "\"}");
        assertThat(socket.next().path("type").asText()).isEqualTo("authenticated");
        return socket;
    }

    private Socket service() throws Exception {
        Socket socket = track(Socket.connect(port, SERVICE_PATH, objectMapper));
        socket.send("{\"type\":\"auth\",\"service_token\":\"" + SERVICE_TOKEN + "\"}");
        assertThat(socket.next().path("type").asText()).isEqualTo("authenticated");
        return socket;
    }

    private Socket track(Socket socket) {
        sockets.add(socket);
        return socket;
    }

    private String vm(VmRole role) {
        String vmId = UUID.randomUUID().toString();
        vmRecordRepository.save(new VmRecord(vmId,
                new CreateVmCommand("display-vm-" + vmId.substring(0, 8), "debian-13-arm64", "1.0.0", 1, 256, role),
                Instant.now()));
        return vmId;
    }

    private static JsonNode item(JsonNode snapshot, String displayId) {
        for (JsonNode item : snapshot.path("items")) {
            if (displayId.equals(item.path("display_id").asText())) {
                return item;
            }
        }
        return new ObjectMapper().createObjectNode();
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

        static Socket connect(int port, String path, ObjectMapper objectMapper) throws Exception {
            Socket socket = new Socket(objectMapper);
            socket.session = new StandardWebSocketClient()
                    .execute(socket, "ws://127.0.0.1:" + port + path).get(5, TimeUnit.SECONDS);
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

        /** Skips frames until one matches (snapshots from other changes may arrive in between). */
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
