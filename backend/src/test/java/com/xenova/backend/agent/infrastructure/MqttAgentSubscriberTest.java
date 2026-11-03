package com.xenova.backend.agent.infrastructure;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;

import com.xenova.backend.agent.application.AgentMessageHandler;
import com.xenova.backend.agent.application.TelemetryHeartbeatMessageHandler;
import com.xenova.backend.agent.application.ZephyrTelemetryMessageHandler;
import com.xenova.backend.event.application.RecoveryEventMessageHandler;
import com.xenova.backend.event.application.RecoveryEventBackfillService;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class MqttAgentSubscriberTest {

    private static final Instant RECEIVED_AT = Instant.parse("2026-10-27T06:00:00Z");

    private AgentMessageHandler agentHandler;
    private ZephyrTelemetryMessageHandler telemetryHandler;
    private TelemetryHeartbeatMessageHandler heartbeatHandler;
    private RecoveryEventMessageHandler recoveryHandler;
    private RecoveryEventBackfillService backfillService;
    private MqttAgentSubscriber subscriber;

    @BeforeEach
    void setUp() {
        agentHandler = mock(AgentMessageHandler.class);
        telemetryHandler = mock(ZephyrTelemetryMessageHandler.class);
        heartbeatHandler = mock(TelemetryHeartbeatMessageHandler.class);
        recoveryHandler = mock(RecoveryEventMessageHandler.class);
        backfillService = mock(RecoveryEventBackfillService.class);
        MqttProperties properties = new MqttProperties(
                true, "tcp://127.0.0.1:1883", "test-client", null, null, Duration.ofSeconds(1));
        subscriber = new MqttAgentSubscriber(
                properties, agentHandler, telemetryHandler, heartbeatHandler, recoveryHandler, backfillService,
                Clock.fixed(RECEIVED_AT, ZoneOffset.UTC));
    }

    @Test
    void subscribesToRecoveryEventsWithQosOne() {
        assertThat(MqttAgentSubscriber.TOPICS)
                .contains(RecoveryEventMessageHandler.TOPIC);
        int recoveryTopicIndex = java.util.Arrays.asList(MqttAgentSubscriber.TOPICS)
                .indexOf(RecoveryEventMessageHandler.TOPIC);
        assertThat(MqttAgentSubscriber.QOS[recoveryTopicIndex]).isEqualTo(1);
    }

    @Test
    void routesRecoveryTopicToRecoveryHandler() {
        byte[] payload = "recovery-event".getBytes(java.nio.charset.StandardCharsets.UTF_8);

        subscriber.messageArrived(RecoveryEventMessageHandler.TOPIC, new MqttMessage(payload));

        verify(recoveryHandler).handle(payload, RECEIVED_AT);
        verifyNoInteractions(agentHandler);
    }

    @Test
    void keepsRoutingAgentTopicsToAgentHandler() {
        byte[] payload = "heartbeat".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        String topic = AgentMessageHandler.TOPIC_PREFIX + "vm-1/heartbeat";

        subscriber.messageArrived(topic, new MqttMessage(payload));

        verify(agentHandler).handle(topic, payload, RECEIVED_AT);
        verifyNoInteractions(recoveryHandler, telemetryHandler);
    }

    @Test
    void routesZephyrTelemetryMetricsToTelemetryHandler() {
        byte[] payload = "telemetry".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        String topic = "xenova/v2/vms/vm-1/telemetry/metrics";
        int index = java.util.Arrays.asList(MqttAgentSubscriber.TOPICS).indexOf(ZephyrTelemetryMessageHandler.TOPIC);

        subscriber.messageArrived(topic, new MqttMessage(payload));

        assertThat(MqttAgentSubscriber.QOS[index]).isZero();
        verify(telemetryHandler).handle(topic, payload, RECEIVED_AT);
        verifyNoInteractions(agentHandler, recoveryHandler);
    }

    @Test
    void routesTelemetryHeartbeatWithRetainFlagAndQosZero() {
        String topic = "xenova/v2/vms/f1000000-0000-4000-8000-000000000001/telemetry/heartbeat";
        byte[] payload = "heartbeat".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        MqttMessage message = new MqttMessage(payload);
        message.setRetained(true);

        subscriber.messageArrived(topic, message);

        int index = java.util.Arrays.asList(MqttAgentSubscriber.TOPICS).indexOf(TelemetryHeartbeatMessageHandler.TOPIC);
        assertThat(MqttAgentSubscriber.TOPICS).hasSameSizeAs(MqttAgentSubscriber.QOS);
        assertThat(MqttAgentSubscriber.QOS[index]).isZero();
        verify(heartbeatHandler).handle(topic, payload, true, RECEIVED_AT);
        verifyNoInteractions(agentHandler, telemetryHandler, recoveryHandler);
    }
}
