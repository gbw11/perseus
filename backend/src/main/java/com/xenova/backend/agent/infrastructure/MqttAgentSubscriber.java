package com.xenova.backend.agent.infrastructure;

import com.xenova.backend.agent.application.AgentMessageHandler;
import com.xenova.backend.agent.application.ZephyrTelemetryMessageHandler;
import com.xenova.backend.event.application.RecoveryEventMessageHandler;
import com.xenova.backend.event.application.RecoveryEventBackfillService;
import java.time.Clock;
import java.time.Instant;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import org.eclipse.paho.client.mqttv3.IMqttDeliveryToken;
import org.eclipse.paho.client.mqttv3.MqttCallbackExtended;
import org.eclipse.paho.client.mqttv3.MqttClient;
import org.eclipse.paho.client.mqttv3.MqttConnectOptions;
import org.eclipse.paho.client.mqttv3.MqttException;
import org.eclipse.paho.client.mqttv3.MqttMessage;
import org.eclipse.paho.client.mqttv3.persist.MemoryPersistence;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.SmartLifecycle;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

// Subscribes to agent topics on the MQTT broker. The application starts even when the broker is down;
// the first connection is retried until it succeeds, after which Paho reconnects on its own.
@Component
@ConditionalOnProperty(prefix = "xenova.mqtt", name = "enabled", havingValue = "true")
@EnableConfigurationProperties(MqttProperties.class)
public class MqttAgentSubscriber implements SmartLifecycle, MqttCallbackExtended {

    // Heartbeat and inventory use QoS 1, metrics QoS 0 (MQTT-01/02/03). Zephyr telemetry metrics are QoS 0 too.
    static final String[] TOPICS = {
            AgentMessageHandler.TOPIC_PREFIX + "+/heartbeat",
            AgentMessageHandler.TOPIC_PREFIX + "+/inventory",
            AgentMessageHandler.TOPIC_PREFIX + "+/metrics",
            RecoveryEventMessageHandler.TOPIC,
            ZephyrTelemetryMessageHandler.TOPIC
    };
    static final int[] QOS = {1, 1, 0, 1, 0};

    private static final Logger log = LoggerFactory.getLogger(MqttAgentSubscriber.class);

    private final MqttProperties properties;
    private final AgentMessageHandler handler;
    private final ZephyrTelemetryMessageHandler telemetryHandler;
    private final RecoveryEventMessageHandler recoveryEventHandler;
    private final RecoveryEventBackfillService recoveryEventBackfillService;
    private final Clock clock;
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "mqtt-agent-subscriber");
        thread.setDaemon(true);
        return thread;
    });

    private volatile MqttClient client;
    private volatile boolean running;

    public MqttAgentSubscriber(
            MqttProperties properties,
            AgentMessageHandler handler,
            ZephyrTelemetryMessageHandler telemetryHandler,
            RecoveryEventMessageHandler recoveryEventHandler,
            RecoveryEventBackfillService recoveryEventBackfillService,
            Clock clock) {
        this.properties = properties;
        this.handler = handler;
        this.telemetryHandler = telemetryHandler;
        this.recoveryEventHandler = recoveryEventHandler;
        this.recoveryEventBackfillService = recoveryEventBackfillService;
        this.clock = clock;
    }

    @Override
    public void start() {
        try {
            client = new MqttClient(properties.brokerUrl(), properties.clientId(), new MemoryPersistence());
        } catch (MqttException exception) {
            throw new IllegalStateException("Invalid MQTT broker settings: " + properties.brokerUrl(), exception);
        }
        client.setCallback(this);
        running = true;
        scheduler.execute(this::connect);
    }

    private void connect() {
        if (!running) {
            return;
        }
        try {
            client.connect(connectOptions());
        } catch (MqttException exception) {
            log.warn("MQTT broker {} unavailable ({}), retrying in {}",
                    properties.brokerUrl(), exception.getMessage(), properties.reconnectInterval());
            scheduler.schedule(this::connect, properties.reconnectInterval().toMillis(), TimeUnit.MILLISECONDS);
        }
    }

    private MqttConnectOptions connectOptions() {
        MqttConnectOptions options = new MqttConnectOptions();
        // A clean session drops backlog: stale heartbeats queued while offline are worthless.
        options.setCleanSession(true);
        options.setAutomaticReconnect(true);
        options.setMaxReconnectDelay((int) properties.reconnectInterval().toMillis());
        options.setConnectionTimeout(10);
        options.setKeepAliveInterval(30);
        if (StringUtils.hasText(properties.username())) {
            options.setUserName(properties.username());
            options.setPassword(properties.password() == null ? new char[0] : properties.password().toCharArray());
        }
        return options;
    }

    // Subscriptions are lost with a clean session, so they are renewed after every (re)connect.
    // Subscribing on the Paho callback thread can deadlock, hence the hand-off to the scheduler.
    @Override
    public void connectComplete(boolean reconnect, String serverUri) {
        log.info("MQTT {} to {}", reconnect ? "reconnected" : "connected", serverUri);
        scheduler.execute(() -> {
            try {
                client.subscribe(TOPICS, QOS);
                log.info("MQTT subscribed to {} topic filters", TOPICS.length);
                recoveryEventBackfillService.requestBackfill();
            } catch (MqttException exception) {
                log.error("MQTT subscribe failed", exception);
            }
        });
    }

    @Override
    public void connectionLost(Throwable cause) {
        log.warn("MQTT connection lost: {}", cause == null ? "unknown" : cause.getMessage());
    }

    @Override
    public void messageArrived(String topic, MqttMessage message) {
        Instant receivedAt = clock.instant();
        if (RecoveryEventMessageHandler.TOPIC.equals(topic)) {
            recoveryEventHandler.handle(message.getPayload(), receivedAt);
            return;
        }
        if (ZephyrTelemetryMessageHandler.matches(topic)) {
            telemetryHandler.handle(topic, message.getPayload(), receivedAt);
            return;
        }
        handler.handle(topic, message.getPayload(), receivedAt);
    }

    @Override
    public void deliveryComplete(IMqttDeliveryToken token) {
        // This client only subscribes.
    }

    @Override
    public void stop() {
        running = false;
        scheduler.shutdownNow();
        MqttClient current = client;
        if (current == null) {
            return;
        }
        try {
            if (current.isConnected()) {
                current.disconnect();
            }
            current.close();
        } catch (MqttException exception) {
            log.warn("MQTT shutdown failed: {}", exception.getMessage());
        }
    }

    @Override
    public boolean isRunning() {
        return running;
    }
}
