package com.xenova.backend.agent.infrastructure;

import com.xenova.backend.agent.application.AgentMessageHandler;
import com.xenova.backend.agent.application.AgentCommandPublisher;
import com.xenova.backend.agent.application.CommandResultMessageHandler;
import com.xenova.backend.agent.application.RetainedInventoryClearer;
import com.xenova.backend.agent.application.TelemetryHeartbeatMessageHandler;
import com.xenova.backend.agent.application.VmTelemetryMessageHandler;
import com.xenova.backend.event.application.RecoveryEventMessageHandler;
import com.xenova.backend.event.application.RecoveryEventBackfillService;
import java.time.Clock;
import java.time.Instant;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
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
public class MqttAgentSubscriber
        implements SmartLifecycle, MqttCallbackExtended, AgentCommandPublisher, RetainedInventoryClearer {

    // Heartbeat and inventory use QoS 1, metrics QoS 0 (MQTT-01/02/03). v2 telemetry metrics and heartbeat are QoS 0.
    static final String[] TOPICS = {
            AgentMessageHandler.TOPIC_PREFIX + "+/heartbeat",
            AgentMessageHandler.TOPIC_PREFIX + "+/inventory",
            AgentMessageHandler.TOPIC_PREFIX + "+/metrics",
            RecoveryEventMessageHandler.TOPIC,
            VmTelemetryMessageHandler.METRICS_TOPIC,
            TelemetryHeartbeatMessageHandler.TOPIC,
            CommandResultMessageHandler.TOPIC
    };
    static final int[] QOS = {1, 1, 0, 1, 0, 0, 1};
    static final long ACTION_TIMEOUT_MS = 10_000;

    private static final Logger log = LoggerFactory.getLogger(MqttAgentSubscriber.class);

    private final MqttProperties properties;
    private final AgentMessageHandler handler;
    private final VmTelemetryMessageHandler telemetryHandler;
    private final TelemetryHeartbeatMessageHandler telemetryHeartbeatHandler;
    private final CommandResultMessageHandler commandResultHandler;
    private final RecoveryEventMessageHandler recoveryEventHandler;
    private final RecoveryEventBackfillService recoveryEventBackfillService;
    private final Clock clock;
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "mqtt-agent-subscriber");
        thread.setDaemon(true);
        return thread;
    });

    // Retained inventory clears that could not be sent yet; retried after every (re)connect.
    private final Set<String> pendingInventoryClears = ConcurrentHashMap.newKeySet();

    private volatile MqttClient client;
    private volatile boolean running;

    public MqttAgentSubscriber(
            MqttProperties properties,
            AgentMessageHandler handler,
            VmTelemetryMessageHandler telemetryHandler,
            TelemetryHeartbeatMessageHandler telemetryHeartbeatHandler,
            CommandResultMessageHandler commandResultHandler,
            RecoveryEventMessageHandler recoveryEventHandler,
            RecoveryEventBackfillService recoveryEventBackfillService,
            Clock clock) {
        this.properties = properties;
        this.handler = handler;
        this.telemetryHandler = telemetryHandler;
        this.telemetryHeartbeatHandler = telemetryHeartbeatHandler;
        this.commandResultHandler = commandResultHandler;
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
        // A QoS 1 publish (retained inventory clear) waits for PUBACK; a stalled broker must not hold the scheduler.
        client.setTimeToWait(ACTION_TIMEOUT_MS);
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
                pendingInventoryClears.forEach(this::publishInventoryClear);
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
        if (VmTelemetryMessageHandler.matches(topic)) {
            telemetryHandler.handle(topic, message.getPayload(), receivedAt);
            return;
        }
        if (TelemetryHeartbeatMessageHandler.matches(topic)) {
            telemetryHeartbeatHandler.handle(topic, message.getPayload(), message.isRetained(), receivedAt);
            return;
        }
        if (CommandResultMessageHandler.matches(topic)) {
            commandResultHandler.handle(topic, message.getPayload(), message.isRetained(), receivedAt);
            return;
        }
        if (message.getPayload().length == 0 && topic.endsWith("/inventory")) {
            // The broker forwards our own retained-inventory clear (or anyone's) as an empty message: not a report.
            return;
        }
        handler.handle(topic, message.getPayload(), receivedAt);
    }

    // Contract §3: deleting a VM removes its retained inventory. An empty retained QoS 1 message clears it.
    // Sent on the scheduler so the HTTP delete never waits for the broker.
    @Override
    public void clearRetainedInventory(String vmId) {
        pendingInventoryClears.add(vmId);
        try {
            scheduler.execute(() -> publishInventoryClear(vmId));
        } catch (RuntimeException exception) {
            log.warn("Retained inventory clear for VM {} not scheduled: {}", vmId, exception.getMessage());
        }
    }

    private void publishInventoryClear(String vmId) {
        MqttClient current = client;
        if (current == null || !current.isConnected()) {
            log.warn("MQTT not connected: retained inventory of deleted VM {} will be cleared after reconnecting",
                    vmId);
            return;
        }
        try {
            current.publish(inventoryTopic(vmId), new byte[0], 1, true);
            pendingInventoryClears.remove(vmId);
            log.info("Cleared retained inventory of deleted VM {}", vmId);
        } catch (MqttException exception) {
            log.warn("Clearing retained inventory of deleted VM {} failed ({}), retrying after reconnect",
                    vmId, exception.getMessage());
        }
    }

    static String inventoryTopic(String vmId) {
        return AgentMessageHandler.TOPIC_PREFIX + vmId + "/inventory";
    }

    @Override
    public void deliveryComplete(IMqttDeliveryToken token) {
        // agent.command is QoS 1; command-result is the application-level answer.
        // Retained inventory clears are confirmed synchronously in publishInventoryClear.
    }

    @Override
    public boolean publish(String topic, byte[] payload, int qos) {
        MqttClient current = client;
        if (current == null || !current.isConnected()) {
            return false;
        }
        try {
            MqttMessage message = new MqttMessage(payload);
            message.setQos(qos);
            message.setRetained(false);
            current.publish(topic, message);
            return true;
        } catch (MqttException exception) {
            log.warn("MQTT agent.command publish to {} failed: {}", topic, exception.getMessage());
            return false;
        }
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
