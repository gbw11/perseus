package com.xenova.backend.drive.infrastructure;

import com.xenova.backend.agent.infrastructure.MqttProperties;
import com.xenova.backend.drive.application.DriveEventLogger;
import com.xenova.backend.drive.application.DriveInputPublisher;
import com.xenova.backend.drive.application.DriveStateTracker;
import java.util.concurrent.ExecutorService;
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

// The drive relay's own MQTT connection (S15P31E110-154): subscribes to the gateway, ESP32 and app topics and
// publishes manual input. It is separate from the agent subscriber so that 50 Hz drive traffic never delays
// heartbeats, and uses "{client-id}-drive" so the two connections do not kick each other off the broker.
// Same start-up behaviour: the application starts without a broker and keeps retrying.
@Component
@ConditionalOnProperty(prefix = "xenova.mqtt", name = "enabled", havingValue = "true")
public class MqttDriveClient implements SmartLifecycle, MqttCallbackExtended, DriveInputPublisher {

    // QoS is the subscription maximum: vehicle/control is QoS 1 retained, the rest arrive at their own QoS.
    static final String[] TOPICS = {
            DriveStateTracker.CONTROL_TOPIC, DriveStateTracker.STATUS_TOPIC, DriveStateTracker.APP_EVENTS_FILTER,
            DriveStateTracker.VM_APP_EVENTS_FILTER};
    static final int[] QOS = {1, 0, 1, 0};

    private static final Logger log = LoggerFactory.getLogger(MqttDriveClient.class);
    // Matches the 10 s connection timeout below.
    static final long ACTION_TIMEOUT_MS = 10_000;

    private final MqttProperties properties;
    private final DriveStateTracker tracker;
    private final DriveEventLogger eventLogger;
    private final ScheduledExecutorService scheduler = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "mqtt-drive-client");
        thread.setDaemon(true);
        return thread;
    });
    // Event storage touches the database; keep it off the Paho callback thread.
    private final ExecutorService recorder = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "mqtt-drive-events");
        thread.setDaemon(true);
        return thread;
    });

    private volatile MqttClient client;
    private volatile boolean running;

    public MqttDriveClient(MqttProperties properties, DriveStateTracker tracker, DriveEventLogger eventLogger) {
        this.properties = properties;
        this.tracker = tracker;
        this.eventLogger = eventLogger;
    }

    @Override
    public void start() {
        try {
            client = new MqttClient(properties.brokerUrl(), properties.clientId() + "-drive", new MemoryPersistence());
        } catch (MqttException exception) {
            throw new IllegalStateException("Invalid MQTT broker settings: " + properties.brokerUrl(), exception);
        }
        client.setCallback(this);
        // A QoS 1 publish (resume request) waits for PUBACK on an HTTP thread inside a synchronized method;
        // without a limit a stalled broker would hang that request and every one queued behind it.
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
            tracker.mqttDisconnected();
            log.warn("MQTT broker {} unavailable for the drive relay ({}), retrying in {}",
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
        // 50 Hz × 2 apps + 15 Hz perception: allow enough QoS 0 messages in flight for manual input bursts.
        options.setMaxInflight(100);
        if (StringUtils.hasText(properties.username())) {
            options.setUserName(properties.username());
            options.setPassword(properties.password() == null ? new char[0] : properties.password().toCharArray());
        }
        return options;
    }

    @Override
    public void connectComplete(boolean reconnect, String serverUri) {
        log.info("MQTT drive relay {} to {}", reconnect ? "reconnected" : "connected", serverUri);
        scheduler.execute(() -> {
            try {
                // Advance the receive generation before SUBSCRIBE: retained control may arrive before it returns.
                tracker.mqttConnected();
                client.subscribe(TOPICS, QOS);
                log.info("MQTT drive relay subscribed to {} topic filters", TOPICS.length);
            } catch (MqttException exception) {
                tracker.mqttDisconnected();
                log.error("MQTT drive subscribe failed", exception);
            }
        });
    }

    @Override
    public void connectionLost(Throwable cause) {
        tracker.mqttDisconnected();
        log.warn("MQTT drive relay connection lost: {}", cause == null ? "unknown" : cause.getMessage());
    }

    @Override
    public void messageArrived(String topic, MqttMessage message) {
        // An exception thrown back to Paho closes the connection, so one bad message must not escape.
        try {
            tracker.accept(topic, message.getPayload(), message.isRetained())
                    .ifPresent(notice -> recorder.execute(() -> eventLogger.record(notice)));
        } catch (RuntimeException exception) {
            log.warn("Dropped MQTT drive message on {}", topic, exception);
        }
    }

    @Override
    public void deliveryComplete(IMqttDeliveryToken token) {
        // QoS 0 input: nothing to track.
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
            log.warn("MQTT drive publish to {} failed: {}", topic, exception.getMessage());
            return false;
        }
    }

    @Override
    public void stop() {
        running = false;
        tracker.mqttDisconnected();
        scheduler.shutdownNow();
        recorder.shutdown();
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
            log.warn("MQTT drive relay shutdown failed: {}", exception.getMessage());
        }
    }

    @Override
    public boolean isRunning() {
        return running;
    }
}
