package com.xenova.backend.sim.infrastructure;

import com.xenova.backend.agent.infrastructure.MqttProperties;
import com.xenova.backend.sim.application.SimCommandPublisher;
import com.xenova.backend.sim.application.SimStateTracker;
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
import org.springframework.context.SmartLifecycle;
import org.springframework.stereotype.Component;
import org.springframework.util.StringUtils;

// The simulator relay's own MQTT connection (S15P31E110-270, -271 publishes sim.command on it). sim.world is up to ~30 KB at track changes, so it
// is kept off the drive relay's connection where 50 Hz commands flow. "{client-id}-sim" so the connections do not
// kick each other off the broker. Like the other relays the application starts without a broker and keeps retrying.
// The simulator topics exist only in the simulator demo setup (contract §11); with a real car nothing arrives.
@Component
@ConditionalOnProperty(prefix = "xenova.mqtt", name = "enabled", havingValue = "true")
public class MqttSimClient implements SmartLifecycle, MqttCallbackExtended, SimCommandPublisher {

    static final String[] TOPICS = {SimStateTracker.POSE_TOPIC, SimStateTracker.WORLD_TOPIC, SimStateTracker.RESULT_TOPIC};
    static final int[] QOS = {0, 1, 1};

    private static final Logger log = LoggerFactory.getLogger(MqttSimClient.class);

    private final MqttProperties properties;
    private final SimStateTracker tracker;
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "mqtt-sim-client");
        thread.setDaemon(true);
        return thread;
    });

    private volatile MqttClient client;
    private volatile boolean running;

    public MqttSimClient(MqttProperties properties, SimStateTracker tracker) {
        this.properties = properties;
        this.tracker = tracker;
    }

    @Override
    public void start() {
        try {
            client = new MqttClient(properties.brokerUrl(), properties.clientId() + "-sim", new MemoryPersistence());
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
            log.warn("MQTT broker {} unavailable for the simulator relay ({}), retrying in {}",
                    properties.brokerUrl(), exception.getMessage(), properties.reconnectInterval());
            scheduler.schedule(this::connect, properties.reconnectInterval().toMillis(), TimeUnit.MILLISECONDS);
        }
    }

    private MqttConnectOptions connectOptions() {
        MqttConnectOptions options = new MqttConnectOptions();
        options.setCleanSession(true); // contract §1: no offline queues
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

    @Override
    public void connectComplete(boolean reconnect, String serverUri) {
        log.info("MQTT simulator relay {} to {}", reconnect ? "reconnected" : "connected", serverUri);
        scheduler.execute(() -> {
            try {
                client.subscribe(TOPICS, QOS);
                log.info("MQTT simulator relay subscribed to {} topics", TOPICS.length);
            } catch (MqttException exception) {
                log.error("MQTT simulator subscribe failed", exception);
            }
        });
    }

    @Override
    public void connectionLost(Throwable cause) {
        log.warn("MQTT simulator relay connection lost: {}", cause == null ? "unknown" : cause.getMessage());
    }

    @Override
    public void messageArrived(String topic, MqttMessage message) {
        // An exception thrown back to Paho closes the connection, so one bad message must not escape.
        try {
            tracker.accept(topic, message.getPayload());
        } catch (RuntimeException exception) {
            log.warn("Dropped MQTT sim message on {}", topic, exception);
        }
    }

    @Override
    public void deliveryComplete(IMqttDeliveryToken token) {
        // sim.command is QoS 1; the result (MQTT-18) is the application-level answer.
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
            current.publish(topic, message);
            return true;
        } catch (MqttException exception) {
            log.warn("MQTT sim.command publish to {} failed: {}", topic, exception.getMessage());
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
            log.warn("MQTT simulator relay shutdown failed: {}", exception.getMessage());
        }
    }

    @Override
    public boolean isRunning() {
        return running;
    }
}
