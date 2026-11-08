package com.xenova.backend.agent.infrastructure;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mockingDetails;
import static org.mockito.Mockito.clearInvocations;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.timeout;

import com.xenova.backend.agent.domain.AgentHeartbeatRepository;
import com.xenova.backend.agent.domain.MetricSampleRepository;
import com.xenova.backend.agent.domain.TelemetryHeartbeatRepository;
import com.xenova.backend.event.application.RecoveryEventMessageHandler;
import com.xenova.backend.event.application.RecoveryEventBackfillService;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import io.moquette.broker.Server;
import io.moquette.broker.config.MemoryConfig;
import java.io.IOException;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.Properties;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;

// End-to-end through a real (embedded) MQTT broker: publish like an agent, expect the row in the database.
@SpringBootTest
class MqttAgentSubscriberIntegrationTest {

    private static final String VM = "7a1f0c2e-0000-4000-8000-000000000001";
    private static final int PORT = freePort();
    private static final Server BROKER = startBroker(PORT);

    @Autowired
    private AgentHeartbeatRepository heartbeatRepository;

    @Autowired
    private MetricSampleRepository metricSamples;

    @Autowired
    private TelemetryHeartbeatRepository telemetryHeartbeats;

    @Autowired
    private VmRecordRepository vmRecords;

    @Autowired
    private MqttAgentSubscriber subscriber;

    @MockitoBean
    private RecoveryEventMessageHandler recoveryEventHandler;

    @MockitoBean
    private RecoveryEventBackfillService recoveryEventBackfillService;

    @DynamicPropertySource
    static void mqttProperties(DynamicPropertyRegistry registry) {
        registry.add("xenova.mqtt.enabled", () -> "true");
        registry.add("xenova.mqtt.broker-url", () -> "tcp://127.0.0.1:" + PORT);
        registry.add("xenova.mqtt.client-id", () -> "xenova-backend-it");
    }

    @AfterAll
    static void stopBroker() {
        BROKER.stopServer();
    }

    @Test
    void requestsRecoveryBackfillAfterConnecting() {
        clearInvocations(recoveryEventBackfillService);
        subscriber.connectComplete(true, "tcp://127.0.0.1:" + PORT);

        verify(recoveryEventBackfillService, timeout(10_000)).requestBackfill();
    }

    @Test
    void heartbeatPublishedToBrokerIsStored() throws Exception {
        String heartbeat = """
                {"schema_version":"1.0","message_id":"m-1","vm_id":"%s","boot_id":"boot-1",
                 "agent_instance_id":"agent-1","type":"agent.heartbeat","seq":1,
                 "sent_at":"%s","payload":{"uptime_ms":1000,"agent_version":"0.1.0"}}
                """.formatted(VM, Instant.now());

        // The subscriber connects asynchronously, so keep publishing until it is subscribed.
        try (MqttClient agent = new MqttClient("tcp://127.0.0.1:" + PORT, "fake-agent", new MemoryPersistence())) {
            agent.connect();
            Instant deadline = Instant.now().plus(Duration.ofSeconds(10));
            while (heartbeatRepository.findById(VM).isEmpty() && Instant.now().isBefore(deadline)) {
                MqttMessage message = new MqttMessage(heartbeat.getBytes(StandardCharsets.UTF_8));
                message.setQos(1);
                agent.publish("xenova/v1/vms/" + VM + "/heartbeat", message);
                Thread.sleep(200);
            }
            agent.disconnect();
        }

        assertThat(heartbeatRepository.findById(VM)).isPresent();
        assertThat(heartbeatRepository.findById(VM).orElseThrow().getBootId()).isEqualTo("boot-1");
    }

    @Test
    void recoveryEventPublishedToBrokerIsRoutedToRecoveryHandler() throws Exception {
        byte[] payload = "{\"event_id\":\"event-1\",\"type\":\"recovery.started\"}"
                .getBytes(StandardCharsets.UTF_8);

        try (MqttClient publisher = new MqttClient(
                "tcp://127.0.0.1:" + PORT, "fake-recovery-worker", new MemoryPersistence())) {
            publisher.connect();
            Instant deadline = Instant.now().plus(Duration.ofSeconds(10));
            while (mockingDetails(recoveryEventHandler).getInvocations().isEmpty()
                    && Instant.now().isBefore(deadline)) {
                MqttMessage message = new MqttMessage(payload);
                message.setQos(1);
                publisher.publish(RecoveryEventMessageHandler.TOPIC, message);
                Thread.sleep(200);
            }
            publisher.disconnect();
        }

        verify(recoveryEventHandler).handle(
                org.mockito.ArgumentMatchers.any(byte[].class),
                org.mockito.ArgumentMatchers.any(Instant.class));
    }

    @Test
    void zephyrV1CountersAndV2CpuReachTheirStoragePathsThroughTheBroker() throws Exception {
        String vm = "f1000000-0000-4000-8000-000000000002";
        String messageId = "f4000000-0000-4000-8000-000000000001";
        String network = """
                {"schema_version":"1.0","type":"agent.metrics","vm_id":"%s","message_id":"%s",
                 "boot_id":"f2000000-0000-4000-8000-000000000001","agent_instance_id":"f3000000-0000-4000-8000-000000000001",
                 "seq":1,"sent_at":null,"payload":{"window_ms":null,"observed_uptime_ms":"50000","values":[
                 {"name":"net_rx_bytes_total","unit":"bytes","value":"9007199254740993","unavailable_reason":null,
                  "source_rc":0,"labels":{"iface":"eth0","counter_epoch":"1"}},
                 {"name":"net_tx_bytes_total","unit":"bytes","value":"0","unavailable_reason":null,
                  "source_rc":0,"labels":{"iface":"eth0","counter_epoch":"1"}}]}}
                """.formatted(vm, messageId);
        byte[] cpu = Files.readString(Path.of("../platform/guests/zephyr/telemetry/contract/fixtures-2.1/cpu.json"))
                .replace("f1000000-0000-4000-8000-000000000001", vm).getBytes(StandardCharsets.UTF_8);
        vmRecords.save(VmRecord.registeredByPlatform(vm, "broker-zephyr", "zephyr-fixture", "1", 1, 16,
                GuestKind.ZEPHYR, Instant.now(), null));
        try (MqttClient publisher = new MqttClient(
                "tcp://127.0.0.1:" + PORT, "zephyr-fixture-publisher", new MemoryPersistence())) {
            publisher.connect();
            Instant deadline = Instant.now().plusSeconds(10);
            while (Instant.now().isBefore(deadline)) {
                publisher.publish("xenova/v1/vms/" + vm + "/metrics", network.getBytes(StandardCharsets.UTF_8),
                        0, false);
                publisher.publish("xenova/v2/vms/" + vm + "/telemetry/metrics", cpu, 0, false);
                if (metricSamples.findById(messageId).isPresent()
                        && metricSamples.findAll().stream().anyMatch(sample -> vm.equals(sample.getVmId())
                                && sample.getCpuWindow() != null)) {
                    break;
                }
                Thread.sleep(100);
            }
            publisher.disconnect();
        }
        assertThat(metricSamples.findById(messageId).orElseThrow().getValues().getFirst().counterValue())
                .isEqualTo("9007199254740993");
        assertThat(metricSamples.findAll()).anyMatch(sample -> sample.getVmId().equals(vm)
                && sample.getCpuWindow() != null && "1".equals(sample.getCpuWindow().sampleSeq()));
    }

    @Test
    void v2PublisherHeartbeatsReachDedicatedStorageThroughMqtt() throws Exception {
        String zephyr = "f1000000-0000-4000-8000-000000000006";
        String linux = "f1000000-0000-4000-8000-000000000007";
        String fixture = Files.readString(Path.of("../platform/guests/zephyr/telemetry/contract/fixtures-2.1/utc.json"));
        String zephyrHeartbeat = fixture.replace("f1000000-0000-4000-8000-000000000001", zephyr);
        String linuxHeartbeat = fixture.replace("f1000000-0000-4000-8000-000000000001", linux)
                .replace("\"2026-01-01T00:00:00.000Z\"", "null").replace("\"synchronized\"", "\"unavailable\"")
                .replace("\"dom0_adjtimex\"", "null").replace("\"uncertainty_ms\": 126", "\"uncertainty_ms\": null")
                .replace("\"running\"", "\"degraded\"");
        try (MqttClient publisher = new MqttClient(
                "tcp://127.0.0.1:" + PORT, "heartbeat-fixture-publisher", new MemoryPersistence())) {
            publisher.connect();
            Instant deadline = Instant.now().plusSeconds(10);
            while (Instant.now().isBefore(deadline)
                    && (telemetryHeartbeats.findById(zephyr).isEmpty() || telemetryHeartbeats.findById(linux).isEmpty())) {
                publisher.publish("xenova/v2/vms/" + zephyr + "/telemetry/heartbeat",
                        zephyrHeartbeat.getBytes(StandardCharsets.UTF_8), 0, false);
                publisher.publish("xenova/v2/vms/" + linux + "/telemetry/heartbeat",
                        linuxHeartbeat.getBytes(StandardCharsets.UTF_8), 0, false);
                Thread.sleep(100);
            }
            publisher.disconnect();
        }
        assertThat(telemetryHeartbeats.findById(zephyr).orElseThrow().report().publisherState()).isEqualTo("running");
        assertThat(telemetryHeartbeats.findById(linux).orElseThrow().report().publisherState()).isEqualTo("degraded");
        assertThat(heartbeatRepository.findAll()).noneMatch(row -> zephyr.equals(row.getVmId()) || linux.equals(row.getVmId()));
    }

    @Test
    void deletedVmRetainedInventoryIsClearedOnTheBroker() throws Exception {
        String vm = "c1000000-0000-4000-8000-000000000003";
        String topic = "xenova/v1/vms/" + vm + "/inventory";
        try (MqttClient agent = new MqttClient("tcp://127.0.0.1:" + PORT, "fake-agent-inventory",
                new MemoryPersistence())) {
            agent.connect();
            agent.publish(topic, "{\"type\":\"agent.inventory\"}".getBytes(StandardCharsets.UTF_8), 1, true);
            agent.disconnect();
        }
        assertThat(retainedPayload(topic)).isNotNull();

        // The subscriber connects asynchronously; a clear sent while disconnected waits for the reconnect.
        Instant deadline = Instant.now().plusSeconds(10);
        byte[] retained;
        do {
            subscriber.clearRetainedInventory(vm);
            Thread.sleep(200);
            retained = retainedPayload(topic);
        } while (retained != null && Instant.now().isBefore(deadline));
        assertThat(retained).isNull();
    }

    // The retained message a new subscriber gets for the topic, or null when there is none.
    private static byte[] retainedPayload(String topic) throws Exception {
        BlockingQueue<byte[]> received = new LinkedBlockingQueue<>();
        try (MqttClient reader = new MqttClient("tcp://127.0.0.1:" + PORT,
                "retained-reader-" + System.nanoTime(), new MemoryPersistence())) {
            reader.connect();
            // Only the retained message can reach a subscriber this new (Moquette does not set its retain flag).
            reader.subscribe(topic, 1, (ignored, message) -> {
                if (message.getPayload().length > 0) {
                    received.add(message.getPayload());
                }
            });
            byte[] payload = received.poll(500, TimeUnit.MILLISECONDS);
            reader.disconnect();
            return payload;
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
