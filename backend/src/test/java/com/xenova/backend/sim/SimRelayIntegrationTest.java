package com.xenova.backend.sim;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.sim.application.SimCommandService;
import com.xenova.backend.sim.application.SimStateTracker;
import io.moquette.broker.Server;
import io.moquette.broker.config.MemoryConfig;
import java.io.IOException;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.Properties;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
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
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.client.standard.StandardWebSocketClient;
import org.springframework.web.socket.handler.TextWebSocketHandler;

// S15P31E110-270·-271 end to end through an embedded broker: simulator topics in (MQTT contract §12) →
// REST-30 GET /api/v1/sim/state and WS-06 /ws/v1/sim (sim.snapshot, sim.command.result) out; REST-31~37 → sim.command.
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class SimRelayIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"sim-relay-user","nickname":"시뮬레이터 중계 테스트","password":"password123!"}
            """;
    private static final int MQTT_PORT = freePort();
    private static final Server BROKER = startBroker(MQTT_PORT);
    private static final AtomicLong SEQ = new AtomicLong();
    private static final String WORLD = """
            {"revision":%d,"track":{"shape":"centerline","id":"spielberg","name":"레드불링","real_m":4315,
             "lane_width_m":0.6,"curb_m":0.06,"centerline":[[0,0],[1,0],[1,1]],"wall_left_m":[0.4,0.4,0.4],
             "wall_right_m":[0.4,0.4,0.4],"start_index":0},"obstacles":[],"estop":false}
            """;

    @LocalServerPort
    private int port;

    @Autowired
    private TestRestTemplate restTemplate;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private SimStateTracker tracker;

    private MqttClient fake;

    @DynamicPropertySource
    static void mqttProperties(DynamicPropertyRegistry registry) {
        registry.add("xenova.mqtt.enabled", () -> "true");
        registry.add("xenova.mqtt.broker-url", () -> "tcp://127.0.0.1:" + MQTT_PORT);
        registry.add("xenova.mqtt.client-id", () -> "xenova-backend-sim-it");
    }

    @BeforeEach
    void connectFakeSimulator() throws Exception {
        fake = new MqttClient("tcp://127.0.0.1:" + MQTT_PORT, "fake-simulator", new MemoryPersistence());
        fake.connect();
    }

    @AfterEach
    void disconnectFakeSimulator() throws Exception {
        fake.disconnect();
        fake.close();
    }

    @AfterAll
    static void stopBroker() {
        BROKER.stopServer();
    }

    @Test
    void stateRequiresLogin() {
        ResponseEntity<String> response = restTemplate.getForEntity("/api/v1/sim/state", String.class);
        assertThat(response.getStatusCode()).isEqualTo(HttpStatus.UNAUTHORIZED);
    }

    @Test
    void poseAndWorldBecomeTheSimState() throws Exception {
        await(() -> {
            publish(SimStateTracker.WORLD_TOPIC, "sim.world", WORLD.formatted(2), 1, true);
            publish(SimStateTracker.POSE_TOPIC, "sim.pose",
                    "{\"x\":6.5,\"y\":-5.6,\"yaw\":3.4,\"v_mps\":0.4,\"steer_rad\":0.0,\"world_revision\":2}", 0, false);
            var state = tracker.snapshot();
            return state.available() && state.world() != null;
        });

        ResponseEntity<String> response = restTemplate.exchange(
                "/api/v1/sim/state", HttpMethod.GET, new HttpEntity<>(bearer(login())), String.class);
        assertThat(response.getStatusCode().is2xxSuccessful()).isTrue();
        JsonNode state = objectMapper.readTree(response.getBody());
        assertThat(state.path("available").asBoolean()).isTrue();
        assertThat(state.path("pose").path("x").asDouble()).isEqualTo(6.5);
        assertThat(state.path("world").path("track").path("lane_width_m").asDouble()).isEqualTo(0.6);
        assertThat(state.path("pose_age_ms").asLong()).isBetween(0L, 1000L);
    }

    @Test
    void socketPushesSnapshotsAndCommandResults() throws Exception {
        Socket socket = Socket.open(port, "/ws/v1/sim", login(), objectMapper);
        JsonNode first = socket.next();
        assertThat(first.path("type").asText()).isEqualTo("sim.snapshot");
        assertThat(first.has("available") && first.has("pose") && first.has("world")).isTrue();

        // 10 Hz while connected: keep publishing until a snapshot carries this world and pose
        Instant deadline = Instant.now().plus(Duration.ofSeconds(10));
        JsonNode frame;
        do {
            publish(SimStateTracker.WORLD_TOPIC, "sim.world", WORLD.formatted(7), 1, true);
            publish(SimStateTracker.POSE_TOPIC, "sim.pose",
                    "{\"x\":1.0,\"y\":2.0,\"yaw\":0.5,\"v_mps\":0.0,\"steer_rad\":0.0,\"world_revision\":7}", 0, false);
            frame = socket.next();
            assertThat(Instant.now()).isBefore(deadline);
        } while (!(frame.path("available").asBoolean() && frame.path("world").path("revision").asLong() == 7
                && frame.path("pose").path("yaw").asDouble() == 0.5)); // other tests' late poses may still arrive

        publish(SimStateTracker.RESULT_TOPIC, "sim.command.result",
                "{\"request_id\":\"r-42\",\"op\":\"obstacle.add\",\"ok\":false,\"code\":\"NO_GAP\","
                        + "\"message\":\"차가 지나갈 틈이 없어요\",\"gap_m\":0.2,\"need_m\":0.26}", 1, false);
        JsonNode result;
        do {
            result = socket.next();
            assertThat(Instant.now()).isBefore(deadline.plusSeconds(5));
        } while (!"sim.command.result".equals(result.path("type").asText()));
        assertThat(result.path("request_id").asText()).isEqualTo("r-42");
        assertThat(result.path("code").asText()).isEqualTo("NO_GAP");
        assertThat(result.path("need_m").asDouble()).isEqualTo(0.26);
    }

    @Test
    void consoleControlsBecomeSimCommands() throws Exception {
        BlockingQueue<JsonNode> commands = new LinkedBlockingQueue<>();
        fake.subscribe(SimCommandService.COMMAND_TOPIC, 1, (topic, message) ->
                commands.add(objectMapper.readTree(message.getPayload())));
        String obstacles = "[{\"id\":\"o-1\",\"kind\":\"box\",\"x\":1.0,\"y\":2.0,\"w\":0.3}]";
        await(() -> {
            publish(SimStateTracker.WORLD_TOPIC, "sim.world",
                    WORLD.formatted(9).replace("\"obstacles\":[]", "\"obstacles\":" + obstacles), 1, true);
            publish(SimStateTracker.POSE_TOPIC, "sim.pose",
                    "{\"x\":1.0,\"y\":0.0,\"yaw\":0.0,\"v_mps\":0.0,\"steer_rad\":0.0,\"world_revision\":9}", 0, false);
            var state = tracker.snapshot();
            return state.available() && state.world() != null && state.world().path("revision").asLong() == 9;
        });
        // A running simulator keeps publishing sim.pose at 20 Hz; without it the backend answers 409 after one second.
        ScheduledExecutorService poses = Executors.newSingleThreadScheduledExecutor();
        poses.scheduleAtFixedRate(() -> publish(SimStateTracker.POSE_TOPIC, "sim.pose",
                "{\"x\":1.0,\"y\":0.0,\"yaw\":0.0,\"v_mps\":0.0,\"steer_rad\":0.0,\"world_revision\":9}", 0, false),
                0, 50, TimeUnit.MILLISECONDS);
        try {
            controlsWhileRunning(commands);
        } finally {
            poses.shutdownNow();
        }
    }

    private void controlsWhileRunning(BlockingQueue<JsonNode> commands) throws Exception {
        HttpHeaders headers = bearer(login());
        headers.setContentType(MediaType.APPLICATION_JSON);

        ResponseEntity<String> added = restTemplate.exchange("/api/v1/sim/obstacles", HttpMethod.POST,
                new HttpEntity<>("{\"kind\":\"cone\",\"x\":3.5,\"y\":-1.25}", headers), String.class);
        assertThat(added.getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        JsonNode body = objectMapper.readTree(added.getBody());
        JsonNode command = commands.poll(5, TimeUnit.SECONDS);
        assertThat(command).isNotNull();
        assertThat(command.path("type").asText()).isEqualTo("sim.command");
        assertThat(command.path("payload").path("op").asText()).isEqualTo("obstacle.add");
        assertThat(command.path("payload").path("obstacle_id").asText()).isEqualTo(body.path("obstacle_id").asText());
        assertThat(command.path("payload").path("request_id").asText()).isEqualTo(body.path("request_id").asText());

        assertThat(restTemplate.exchange("/api/v1/sim/obstacles/o-1", HttpMethod.PUT,
                new HttpEntity<>("{\"x\":1.5,\"y\":2.0}", headers), String.class).getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        assertThat(commands.poll(5, TimeUnit.SECONDS).path("payload").path("op").asText()).isEqualTo("obstacle.move");
        assertThat(restTemplate.exchange("/api/v1/sim/obstacles/o-1", HttpMethod.DELETE,
                new HttpEntity<>(headers), String.class).getStatusCode()).isEqualTo(HttpStatus.ACCEPTED);
        assertThat(commands.poll(5, TimeUnit.SECONDS).path("payload").path("op").asText()).isEqualTo("obstacle.remove");
        for (String[] call : new String[][] {
                {"/api/v1/sim/estop", "{\"engaged\":true}", "estop"},
                {"/api/v1/sim/push", "{\"dy_m\":0.2}", "push"},
                {"/api/v1/sim/track", "{\"track_id\":\"monza\"}", "track.select"},
                {"/api/v1/sim/race/start", "", "race.start"}}) {
            ResponseEntity<String> response = restTemplate.exchange(call[0], HttpMethod.POST,
                    new HttpEntity<>(call[1], headers), String.class);
            assertThat(response.getStatusCode()).as(call[0]).isEqualTo(HttpStatus.ACCEPTED);
            assertThat(commands.poll(5, TimeUnit.SECONDS).path("payload").path("op").asText()).isEqualTo(call[2]);
        }

        ResponseEntity<String> missing = restTemplate.exchange("/api/v1/sim/obstacles/o-404", HttpMethod.DELETE,
                new HttpEntity<>(headers), String.class);
        assertThat(missing.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND);
        assertThat(objectMapper.readTree(missing.getBody()).path("code").asText()).isEqualTo("OBSTACLE_NOT_FOUND");
        ResponseEntity<String> tooFar = restTemplate.exchange("/api/v1/sim/push", HttpMethod.POST,
                new HttpEntity<>("{\"dy_m\":0.9}", headers), String.class);
        assertThat(tooFar.getStatusCode()).isEqualTo(HttpStatus.UNPROCESSABLE_ENTITY);
        ResponseEntity<String> notNumber = restTemplate.exchange("/api/v1/sim/obstacles", HttpMethod.POST,
                new HttpEntity<>("{\"x\":\"a\",\"y\":1}", headers), String.class);
        assertThat(notNumber.getStatusCode()).isEqualTo(HttpStatus.UNPROCESSABLE_ENTITY);
        assertThat(commands.poll(500, TimeUnit.MILLISECONDS)).as("refused calls send nothing").isNull();
    }

    // ---- helpers ------------------------------------------------------------------------------------

    private synchronized void publish(String topic, String type, String payload, int qos, boolean retained) {
        String json = """
                {"schema_version":"1.0","message_id":"sim-it-%d","source_id":"simulator","boot_id":"it-boot",
                 "agent_instance_id":"it-run","type":"%s","seq":%d,"sent_at":"%s","payload":%s}
                """.formatted(SEQ.incrementAndGet(), type, SEQ.get(), Instant.now(), payload);
        try {
            MqttMessage message = new MqttMessage(json.getBytes(StandardCharsets.UTF_8));
            message.setQos(qos);
            message.setRetained(retained);
            fake.publish(topic, message);
        } catch (Exception exception) {
            throw new IllegalStateException(exception);
        }
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
            socket.session.setTextMessageSizeLimit(256 * 1024);
            socket.session.sendMessage(new TextMessage("{\"type\":\"auth\",\"access_token\":\"" + token + "\"}"));
            assertThat(socket.next().path("type").asText()).isEqualTo("authenticated");
            return socket;
        }

        @Override
        protected void handleTextMessage(WebSocketSession session, TextMessage message) {
            messages.add(message.getPayload());
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
