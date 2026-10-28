package com.xenova.backend.agent.infrastructure;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mockingDetails;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.timeout;

import com.xenova.backend.agent.domain.AgentHeartbeatRepository;
import com.xenova.backend.event.application.RecoveryEventMessageHandler;
import com.xenova.backend.event.application.RecoveryEventBackfillService;
import io.moquette.broker.Server;
import io.moquette.broker.config.MemoryConfig;
import java.io.IOException;
import java.net.ServerSocket;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.Properties;
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
