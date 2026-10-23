package com.xenova.backend.drive;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.drive.application.DriveStateTracker;
import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.event.domain.VmEvent;
import com.xenova.backend.event.domain.VmEventRepository;
import io.moquette.broker.Server;
import io.moquette.broker.config.MemoryConfig;
import java.io.IOException;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.BooleanSupplier;
import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.client.TestRestTemplate;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;

// S15P31E110-154 end to end through an embedded broker: gateway/app messages in → drive state, event log and
// WS push out; console keys in over WS → drive.input on the broker (MQTT contract §5·§6·§7).
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class DriveRelayIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"drive-relay-user","password":"password123!"}
            """;
    private static final int MQTT_PORT = freePort();
    private static final Server BROKER = startBroker(MQTT_PORT);
    private static final AtomicLong SEQ = new AtomicLong();

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate restTemplate;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private DriveStateTracker tracker;

    @Autowired
    private VmEventRepository eventRepository;

    private MqttClient fake;
    private final BlockingQueue<JsonNode> inputs = new LinkedBlockingQueue<>();
    private final BlockingQueue<JsonNode> resumes = new LinkedBlockingQueue<>();

    @DynamicPropertySource
    static void mqttProperties(DynamicPropertyRegistry registry) {
        registry.add("xenova.mqtt.enabled", () -> "true");
        registry.add("xenova.mqtt.broker-url", () -> "tcp://127.0.0.1:" + MQTT_PORT);
        registry.add("xenova.mqtt.client-id", () -> "xenova-backend-drive-it");
    }

    @BeforeEach
    void connectFakeGateway() throws Exception {
        fake = new MqttClient("tcp://127.0.0.1:" + MQTT_PORT, "fake-gateway", new MemoryPersistence());
        fake.connect();
        fake.subscribe(DriveStateTracker.PREFIX + "apps/manual/input", 0, (topic, message) ->
                inputs.add(objectMapper.readTree(message.getPayload())));
        fake.subscribe("xenova/v1/console/resume", 1, (topic, message) ->
                resumes.add(objectMapper.readTree(message.getPayload())));
    }

    @AfterEach
    void disconnectFakeGateway() throws Exception {
        fake.disconnect();
        fake.close();
    }

    @AfterAll
    static void stopBroker() {
        BROKER.stopServer();
    }

    @Test
    void gatewayAndAppMessagesBecomeTheDriveState() throws Exception {
        // The relay subscribes asynchronously, so keep publishing until it has seen the messages.
        await(() -> {
            publish(DriveStateTracker.CONTROL_TOPIC, "dom0-gateway", null, "vehicle.control",
                    "{\"mode\":\"MANUAL\",\"generation\":7,\"active_app\":\"manual\",\"active_vm_id\":null,\"reason\":\"command_timeout\"}",
                    1, true);
            publish("xenova/v1/apps/manual/events", "22222222-2222-4222-8222-222222222222", "manual",
                    "drive.command", "{\"generation\":7,\"v_mps\":0.3,\"steer_rad\":0.0}", 0, false);
            var control = tracker.snapshot().control();
            return control != null && "MANUAL".equals(control.mode()) && tracker.snapshot().apps().containsKey("manual");
        });

        ResponseEntity<String> response = restTemplate.exchange(
                "/api/v1/drive/state", HttpMethod.GET, new HttpEntity<>(bearer(login())), String.class);
        assertThat(response.getStatusCode().is2xxSuccessful()).isTrue();
        JsonNode state = objectMapper.readTree(response.getBody());
        assertThat(state.path("control").path("mode").asText()).isEqualTo("MANUAL");
        assertThat(state.path("control").path("generation").asLong()).isEqualTo(7);
        assertThat(state.path("apps").path("manual").path("vm_id").asText())
                .isEqualTo("22222222-2222-4222-8222-222222222222");
        assertThat(state.path("apps").path("manual").path("last_command").path("v_mps").asDouble()).isEqualTo(0.3);

        JsonNode stats = objectMapper.readTree(restTemplate.exchange(
                "/api/v1/messaging/stats", HttpMethod.GET, new HttpEntity<>(bearer(login())), String.class).getBody());
        assertThat(stats.path("items")).isNotEmpty();
    }

    @Test
    void modeChangeAndFaultAreStoredAsEvents() throws Exception {
        await(() -> {
            publish("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle", "drive.mode.changed",
                    "{\"from\":\"MANUAL\",\"to\":\"SAFE_STOP\",\"generation\":9,\"reason\":\"no_fresh_standby\"}", 1, false);
            publish("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle", "drive.fault.detected",
                    "{\"app_id\":\"autonomous\",\"vm_id\":\"11111111-1111-4111-8111-111111111111\",\"reason\":\"command_timeout\"}",
                    1, false);
            return events(EventType.DRIVE_MODE_CHANGED).stream().anyMatch(e -> e.getSeverity() == EventSeverity.WARNING)
                    && !events(EventType.DRIVE_FAULT_DETECTED).isEmpty();
        });
        VmEvent fault = events(EventType.DRIVE_FAULT_DETECTED).get(0);
        assertThat(fault.getVmId()).isEqualTo("11111111-1111-4111-8111-111111111111");
        assertThat(fault.getSeverity()).isEqualTo(EventSeverity.WARNING);
    }

    @Test
    void stateSocketPushesAndInputSocketRelaysKeys() throws Exception {
        await(() -> {
            publish(DriveStateTracker.CONTROL_TOPIC, "dom0-gateway", null, "vehicle.control",
                    "{\"mode\":\"MANUAL\",\"generation\":11,\"active_app\":\"manual\"}", 1, true);
            var control = tracker.snapshot().control();
            return control != null && control.generation() != null && control.generation() == 11;
        });
        String token = login();

        Socket state = Socket.open(port, "/ws/v1/drive/state", token, objectMapper);
        JsonNode pushed = state.next();
        assertThat(pushed.path("type").asText()).isEqualTo("drive.state");
        assertThat(pushed.path("state").path("control").path("mode").asText()).isEqualTo("MANUAL");
        state.session.close();

        Socket input = Socket.open(port, "/ws/v1/drive", token, objectMapper);
        input.send("{\"type\":\"input\",\"request_id\":\"r-1\",\"keys\":[\"up\",\"ArrowUp\"]}");
        JsonNode ack = input.next();
        assertThat(ack.path("type").asText()).isEqualTo("input.ack");
        assertThat(ack.path("result").asText()).isEqualTo("SENT");
        assertThat(ack.path("keys")).hasSize(1);

        // Held keys repeat at 20 Hz: at least three drive.input within 300 ms
        Thread.sleep(300);
        input.send("{\"type\":\"input\",\"keys\":[]}");
        assertThat(input.next().path("result").asText()).isEqualTo("RELEASED");
        Thread.sleep(200);
        List<JsonNode> received = new java.util.ArrayList<>();
        inputs.drainTo(received);
        assertThat(received.size()).isGreaterThanOrEqualTo(4);
        assertThat(received.get(0).path("type").asText()).isEqualTo("drive.input");
        assertThat(received.get(0).path("source_id").asText()).isEqualTo("dom0-server");
        assertThat(received.get(0).path("payload").path("keys").get(0).asText()).isEqualTo("up");
        assertThat(received.get(received.size() - 1).path("payload").path("keys")).isEmpty();
        input.session.close();
    }

    @Test
    void inputIsDroppedOutsideManual() throws Exception {
        await(() -> {
            publish(DriveStateTracker.CONTROL_TOPIC, "dom0-gateway", null, "vehicle.control",
                    "{\"mode\":\"AUTO\",\"generation\":20,\"active_app\":\"autonomous\"}", 1, true);
            var control = tracker.snapshot().control();
            return control != null && "AUTO".equals(control.mode());
        });
        Socket input = Socket.open(port, "/ws/v1/drive", login(), objectMapper);
        input.send("{\"type\":\"input\",\"keys\":[\"up\"]}");
        assertThat(input.next().path("result").asText()).isEqualTo("NOT_MANUAL");
        Thread.sleep(200);
        assertThat(inputs).isEmpty();
        input.session.close();
    }

    @Test
    void resumeIsPublishedOnlyInAutoReadyAndTheCountdownIsRelayed() throws Exception {
        String token = login();
        await(() -> {
            publish(DriveStateTracker.CONTROL_TOPIC, "dom0-gateway", null, "vehicle.control",
                    "{\"mode\":\"MANUAL\",\"generation\":30,\"active_app\":\"manual\"}", 1, true);
            var control = tracker.snapshot().control();
            return control != null && control.generation() != null && control.generation() == 30;
        });
        ResponseEntity<String> refused = restTemplate.exchange(
                "/api/v1/drive/resume", HttpMethod.POST, new HttpEntity<>(bearer(token)), String.class);
        assertThat(refused.getStatusCode().value()).isEqualTo(409);
        assertThat(objectMapper.readTree(refused.getBody()).path("code").asText()).isEqualTo("NOT_AUTO_READY");
        assertThat(restTemplate.postForEntity("/api/v1/drive/resume", null, String.class).getStatusCode().value())
                .isEqualTo(401);

        await(() -> {
            publish(DriveStateTracker.CONTROL_TOPIC, "dom0-gateway", null, "vehicle.control",
                    "{\"mode\":\"AUTO_READY\",\"generation\":31,\"active_app\":null}", 1, true);
            var control = tracker.snapshot().control();
            return control != null && "AUTO_READY".equals(control.mode());
        });
        ResponseEntity<String> accepted = restTemplate.exchange(
                "/api/v1/drive/resume", HttpMethod.POST, new HttpEntity<>(bearer(token)), String.class);
        assertThat(accepted.getStatusCode().value()).isEqualTo(202);
        assertThat(objectMapper.readTree(accepted.getBody()).path("accepted").asBoolean()).isTrue();
        JsonNode request = resumes.poll(5, TimeUnit.SECONDS);
        assertThat(request).isNotNull();
        assertThat(request.path("type").asText()).isEqualTo("drive.resume.requested");
        assertThat(request.path("source_id").asText()).isEqualTo("dom0-server");
        assertThat(request.path("payload").path("requested_by").asText()).isEqualTo("drive-relay-user");

        // The gateway answers with a countdown: event log + drive state.
        await(() -> {
            publish("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle", "drive.resume.accepted",
                    "{\"switch_in_ms\":3000,\"generation\":31}", 1, false);
            return !events(EventType.DRIVE_RESUME_ACCEPTED).isEmpty();
        });
        JsonNode state = objectMapper.readTree(restTemplate.exchange(
                "/api/v1/drive/state", HttpMethod.GET, new HttpEntity<>(bearer(token)), String.class).getBody());
        assertThat(state.path("resume").path("switch_in_ms").asLong()).isEqualTo(3000);
        assertThat(state.path("resume").has("remaining_ms")).isTrue();
        assertThat(events(EventType.DRIVE_RESUME_ACCEPTED).get(0).getSeverity()).isEqualTo(EventSeverity.INFO);

        await(() -> {
            publish("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle", "drive.resume.cancelled",
                    "{\"reason\":\"mode_changed\"}", 1, false);
            return !events(EventType.DRIVE_RESUME_CANCELLED).isEmpty();
        });
        assertThat(tracker.snapshot().resume()).isNull();
    }

    // ---- helpers ------------------------------------------------------------------------------------

    private void publish(String topic, String publisher, String appId, String type, String payload, int qos,
            boolean retained) {
        boolean vm = publisher.length() == 36;
        String json = """
                {"schema_version":"1.0","message_id":"it-%d","%s":"%s",%s"boot_id":"it-boot",
                 "agent_instance_id":"it-run","type":"%s","seq":%d,"sent_at":"%s","payload":%s}
                """.formatted(SEQ.incrementAndGet(), vm ? "vm_id" : "source_id", publisher,
                appId == null ? "" : "\"app_id\":\"" + appId + "\",", type, SEQ.get(), Instant.now(), payload);
        try {
            MqttMessage message = new MqttMessage(json.getBytes(StandardCharsets.UTF_8));
            message.setQos(qos);
            message.setRetained(retained);
            fake.publish(topic, message);
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
    }

    private List<VmEvent> events(EventType type) {
        return eventRepository.findAll().stream().filter(event -> event.getType() == type).toList();
    }

    private static void await(BooleanSupplier condition) throws InterruptedException {
        Instant deadline = Instant.now().plus(Duration.ofSeconds(10));
        while (!condition.getAsBoolean()) {
            assertThat(Instant.now()).as("condition not met in time").isBefore(deadline);
            Thread.sleep(200);
        }
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
        private final ObjectMapper objectMapper;
        private WebSocketSession session;

        private Socket(ObjectMapper objectMapper) {
            this.objectMapper = objectMapper;
        }

        static Socket open(int port, String path, String token, ObjectMapper objectMapper) throws Exception {
            Socket socket = new Socket(objectMapper);
            socket.session = new StandardWebSocketClient()
                    .execute(socket, "ws://127.0.0.1:" + port + path).get(5, TimeUnit.SECONDS);
            socket.send("{\"type\":\"auth\",\"access_token\":\"" + token + "\"}");
            assertThat(socket.next().path("type").asText()).isEqualTo("authenticated");
            return socket;
        }

        @Override
        protected void handleTextMessage(WebSocketSession session, TextMessage message) {
            messages.add(message.getPayload());
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

    private static int freePort() {
        try (ServerSocket socket = new ServerSocket(0)) {
            return socket.getLocalPort();
        } catch (IOException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static Server startBroker(int port) {
        Properties config = new Properties();
        config.setProperty("host", "127.0.0.1");
        config.setProperty("port", String.valueOf(port));
        config.setProperty("allow_anonymous", "true");
        config.setProperty("persistence_enabled", "false");
        config.setProperty("telemetry_enabled", "false");
        Server server = new Server();
        try {
            server.startServer(new MemoryConfig(config));
        } catch (IOException exception) {
            throw new IllegalStateException("Cannot start embedded MQTT broker", exception);
        }
        return server;
    }
}
