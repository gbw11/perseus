package com.xenova.backend.agent.infrastructure;

import static org.assertj.core.api.Assertions.assertThat;
import static org.awaitility.Awaitility.await;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.xenova.backend.agent.application.AgentDataService;
import com.xenova.backend.agent.application.AgentMessageHandler;
import com.xenova.backend.agent.application.TelemetryHeartbeatMessageHandler;
import com.xenova.backend.agent.application.VmTelemetryMessageHandler;
import com.xenova.backend.agent.domain.AgentHeartbeatRepository;
import com.xenova.backend.agent.domain.AgentInventoryRepository;
import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricSampleData;
import com.xenova.backend.agent.domain.MetricSampleRepository;
import com.xenova.backend.agent.domain.MetricValuesConverter;
import com.xenova.backend.agent.domain.ProcessSnapshotRepository;
import com.xenova.backend.agent.domain.TelemetryHeartbeatRepository;
import com.xenova.backend.event.application.RecoveryEventBackfillService;
import com.xenova.backend.event.application.RecoveryEventMessageHandler;
import com.xenova.backend.metrics.api.MetricsController;
import com.xenova.backend.metrics.application.MetricsQueryService;
import com.xenova.backend.vm.domain.VmRecordRepository;
import io.moquette.broker.Server;
import io.moquette.broker.config.MemoryConfig;
import java.io.IOException;
import java.net.InetAddress;
import java.net.ServerSocket;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.Properties;
import java.util.concurrent.ConcurrentHashMap;
import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;
import org.junit.jupiter.api.Test;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

/**
 * Real loopback MQTT, product subscriber/ingest/storage service and existing REST projection.
 * Only database repositories and unrelated message handlers are replaced; this is not a MySQL/auth test.
 */
class AppMemoryMqttConsumerTest {
    private static final Instant NOW = Instant.parse("2026-11-04T06:00:00Z");
    private static final Clock CLOCK = Clock.fixed(NOW, ZoneOffset.UTC);
    private static final Duration WAIT = Duration.ofSeconds(5); // Isolated test budget, not a product deadline.
    private final ObjectMapper mapper = new ObjectMapper().findAndRegisterModules()
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
    private final MetricValuesConverter converter = new MetricValuesConverter();
    private final Map<String, MetricSample> rows = new ConcurrentHashMap<>();
    private final MetricSampleRepository repository = mock(MetricSampleRepository.class);
    private final ApplicationEventPublisher events = mock(ApplicationEventPublisher.class);

    @Test
    void boardZeroAndOwnerRecoveryReachStoredHistoryThroughTheActualMqttSubscription() throws Exception {
        databaseBoundary();
        AgentDataService data = new AgentDataService(mock(AgentInventoryRepository.class),
                mock(AgentHeartbeatRepository.class), mock(TelemetryHeartbeatRepository.class), repository,
                mock(ProcessSnapshotRepository.class), new AgentProperties(Duration.ofSeconds(15), Duration.ofMinutes(1)),
                events);
        VmTelemetryMessageHandler ingest = spy(new VmTelemetryMessageHandler(mapper, data, mock(VmRecordRepository.class)));
        RecoveryEventBackfillService backfill = mock(RecoveryEventBackfillService.class);
        int port = freeLoopbackPort();
        String url = "tcp://127.0.0.1:" + port;
        Server broker = startBroker(port);
        MqttAgentSubscriber subscriber = new MqttAgentSubscriber(
                new MqttProperties(true, url, "app-memory-product-subscriber", null, null, Duration.ofSeconds(1)),
                mock(AgentMessageHandler.class), ingest, mock(TelemetryHeartbeatMessageHandler.class),
                mock(RecoveryEventMessageHandler.class), backfill, CLOCK);
        MqttClient producer = null;
        try {
            subscriber.start();
            // Product callback invokes this only after its actual SUBACK; no sleep or probe publication is needed.
            await().atMost(WAIT).untilAsserted(() -> verify(backfill).requestBackfill());
            producer = new MqttClient(url, "app-memory-fixture-producer", new MemoryPersistence());
            MqttConnectOptions options = new MqttConnectOptions();
            options.setCleanSession(true);
            options.setAutomaticReconnect(false);
            options.setConnectionTimeout(2);
            producer.connect(options);

            for (String region : List.of("drive_maps", "drive_paths", "drive_search_nodes", "drive_odometry")) {
                publish(producer, "board-app-memory-" + region + ".json");
            }
            await().atMost(WAIT).untilAsserted(() -> assertThat(rows).hasSize(4));
            publish(producer, "board-app-memory-drive_maps.json"); // Exact original ID/body redelivery.
            for (String state : List.of("full", "busy", "released")) {
                publish(producer, "app-memory-" + state + ".json");
            }
            byte[] manualResult = resource("/telemetry-manual-memory/native/manual_result.json");
            JsonNode originalResult = mapper.readTree(manualResult);
            producer.publish("xenova/v2/vms/" + originalResult.path("vm_id").asText() + "/telemetry/metrics",
                    manualResult, 0, false);
            await().atMost(WAIT).untilAsserted(() -> {
                assertThat(rows).hasSize(8);
                verify(ingest, times(9)).handle(any(), any(), any());
                verify(events, times(8)).publishEvent(any(Object.class));
            });

            MockMvc http = MockMvcBuilders.standaloneSetup(new MetricsController(new MetricsQueryService(data, CLOCK)))
                    .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper)).build();
            JsonNode board = history(http, "ea476628-620d-4f43-a3ab-4390dab5e154");
            assertThat(board.path("items")).hasSize(4);
            assertThat(board.path("items").findValuesAsText("region"))
                    .contains("drive_maps", "drive_paths", "drive_search_nodes", "drive_odometry");
            for (JsonNode sample : board.path("items")) {
                assertThat(metric(sample, "memory.app.used_bytes").path("raw_value").asText()).isEqualTo("0");
                assertThat(metric(sample, "memory.app.used_bytes").path("status").asText()).isEqualTo("ok");
                assertThat(sample.path("telemetry").path("source_sample_seq").asText()).isEqualTo("1510");
                assertThat(sample.path("telemetry").path("observed_uptime_ms").asText()).isEqualTo("302303");
                assertThat(sample.path("telemetry").path("context").path("execution_id").asText())
                        .isEqualTo("e8d9b94d-cbfd-47ac-97bb-d41db2dd7118");
            }

            JsonNode owner = history(http, "f1000000-0000-4000-8000-000000000001");
            assertThat(owner.path("items")).hasSize(3);
            JsonNode full = sourceSample(owner, "4");
            JsonNode busy = sourceSample(owner, "6");
            JsonNode released = sourceSample(owner, "8");
            assertThat(metric(full, "memory.app.used_bytes").path("raw_value").asText()).isEqualTo("80472");
            assertThat(metric(busy, "memory.app.used_bytes").path("value").isNull()).isTrue();
            assertThat(metric(busy, "memory.app.used_bytes").path("unavailable_reason").asText()).isEqualTo("owner_busy");
            assertThat(metric(busy, "memory.app.used_bytes").path("source_rc").intValue()).isEqualTo(-16);
            assertThat(metric(released, "memory.app.used_bytes").path("raw_value").asText()).isEqualTo("0");
            assertThat(metric(released, "memory.app.high_water_bytes").path("raw_value").asText()).isEqualTo("80472");
            assertThat(metric(released, "memory.app.capacity_failure_total").path("counter_value").asText()).isEqualTo("1");
            JsonNode manual = history(http, originalResult.path("vm_id").asText()).path("items").get(0);
            assertThat(manual.path("telemetry").path("buffer")).isEqualTo(originalResult.path("payload").path("buffer"));
            assertThat(manual.path("telemetry").path("message_seq")).isEqualTo(originalResult.path("seq"));
            assertThat(metric(manual, "memory.app.used_bytes").path("raw_value").asText()).isEqualTo("2592");
        } finally {
            try {
                if (producer != null) {
                    if (producer.isConnected()) producer.disconnect(1000);
                    producer.close();
                }
            } finally {
                try {
                    subscriber.stop();
                } finally {
                    broker.stopServer();
                }
            }
        }
        assertThat(subscriber.isRunning()).isFalse();
    }

    private void databaseBoundary() {
        when(repository.existsById(any())).thenAnswer(call -> rows.containsKey(call.getArgument(0)));
        when(repository.save(any())).thenAnswer(call -> {
            MetricSample sample = call.getArgument(0);
            // Serialize and deserialize using the product JPA converter before public reads see the row.
            MetricSampleData source = new MetricSampleData(sample.getValues(), sample.getNetwork(), sample.getCpuWindow(),
                    sample.getTelemetry());
            MetricSample restored = new MetricSample(sample.getSampleId(), sample.getVmId(), sample.getBootId(),
                    sample.getSource(), sample.getObservedAt(), sample.getReceivedAt(), sample.getWindowMs(),
                    converter.convertToEntityAttribute(converter.convertToDatabaseColumn(source)));
            rows.put(restored.getSampleId(), restored);
            return restored;
        });
        when(repository.findByVmIdAndObservedAtGreaterThanEqualAndObservedAtLessThanOrderByObservedAtAsc(any(), any(), any()))
                .thenAnswer(call -> rows.values().stream()
                        .filter(sample -> sample.getVmId().equals(call.getArgument(0)))
                        .filter(sample -> !sample.getObservedAt().isBefore(call.getArgument(1)))
                        .filter(sample -> sample.getObservedAt().isBefore(call.getArgument(2)))
                        .sorted(Comparator.comparing(MetricSample::getObservedAt)).toList());
    }

    private void publish(MqttClient producer, String file) throws Exception {
        byte[] bytes = resource("/telemetry-app-memory/" + file);
        String vmId = mapper.readTree(bytes).path("vm_id").asText();
        producer.publish("xenova/v2/vms/" + vmId + "/telemetry/metrics", bytes, 0, false);
    }

    private byte[] resource(String path) throws IOException {
        try (var stream = getClass().getResourceAsStream(path)) {
            if (stream == null) throw new IOException("missing fixture " + path);
            return stream.readAllBytes();
        }
    }

    private JsonNode history(MockMvc http, String vmId) throws Exception {
        String body = http.perform(get("/api/v1/vms/{vmId}/metrics", vmId)
                        .param("source", "zephyr_telemetry")
                        .param("from", "2026-11-04T05:50:00Z").param("to", "2026-11-04T06:01:00Z"))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        JsonNode response = mapper.readTree(body);
        for (JsonNode sample : response.path("items")) {
            assertThat(sample.path("observed_at").isTextual()).isTrue();
            assertThat(Instant.parse(sample.path("observed_at").asText())).isNotNull();
            assertThat(sample.path("received_at").asText()).isEqualTo(NOW.toString());
        }
        return response;
    }

    private static JsonNode sourceSample(JsonNode response, String sequence) {
        for (JsonNode sample : response.path("items")) {
            if (sequence.equals(sample.path("telemetry").path("source_sample_seq").asText())) return sample;
        }
        throw new AssertionError("missing source sample " + sequence);
    }

    private static JsonNode metric(JsonNode sample, String name) {
        for (JsonNode metric : sample.path("values")) {
            if (name.equals(metric.path("name").asText())) return metric;
        }
        throw new AssertionError("missing metric " + name);
    }

    private static int freeLoopbackPort() throws IOException {
        try (ServerSocket socket = new ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))) {
            return socket.getLocalPort();
        }
    }

    private static Server startBroker(int port) throws IOException {
        Properties config = new Properties();
        config.setProperty("host", "127.0.0.1");
        config.setProperty("port", Integer.toString(port));
        config.setProperty("allow_anonymous", "true");
        config.setProperty("persistence_enabled", "false");
        config.setProperty("telemetry_enabled", "false");
        Server server = new Server();
        server.startServer(new MemoryConfig(config));
        return server;
    }
}
