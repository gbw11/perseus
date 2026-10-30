package com.xenova.backend.sim.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;
import java.util.function.LongSupplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

// Latest simulator picture from MQTT contract §12 (S15P31E110-270): MQTT-15 sim.pose (20 Hz), MQTT-16
// sim.world (retained, on change) and MQTT-18 sim.command.result. Payloads are kept as received and handed to
// REST-30 / WS-06 unchanged, so new optional fields reach the screens without a backend change.
// Called on the MQTT callback thread; a bad message is counted and dropped, never thrown.
@Component
public class SimStateTracker {

    public static final String POSE_TOPIC = "xenova/v1/sim/pose";
    public static final String WORLD_TOPIC = "xenova/v1/sim/world";
    public static final String RESULT_TOPIC = "xenova/v1/sim/result";

    /** Contract §11.2: available = the last sim.pose arrived within one second. */
    static final long AVAILABLE_MS = 1000;
    static final int MAX_PAYLOAD_BYTES = 64 * 1024;

    private static final Logger log = LoggerFactory.getLogger(SimStateTracker.class);

    private final ObjectMapper objectMapper;
    private final LongSupplier monotonicMillis;
    private final List<Consumer<JsonNode>> resultListeners = new CopyOnWriteArrayList<>();
    private final List<Runnable> worldListeners = new CopyOnWriteArrayList<>();

    private JsonNode pose;
    private long poseAt;
    private JsonNode world;
    private long worldVersion;
    private long rejected;

    @Autowired
    public SimStateTracker(ObjectMapper objectMapper) {
        this(objectMapper, () -> System.nanoTime() / 1_000_000);
    }

    SimStateTracker(ObjectMapper objectMapper, LongSupplier monotonicMillis) {
        this.objectMapper = objectMapper;
        this.monotonicMillis = monotonicMillis;
    }

    /** MQTT-18 results go straight to the console sockets (WS-06 sim.command.result). */
    public void onResult(Consumer<JsonNode> listener) {
        resultListeners.add(listener);
    }

    /** A new sim.world is pushed at once instead of waiting for the next 10 Hz tick. */
    public void onWorldChange(Runnable listener) {
        worldListeners.add(listener);
    }

    public void accept(String topic, byte[] payload) {
        Optional<JsonNode> result;
        boolean worldChanged;
        synchronized (this) {
            long before = worldVersion;
            try {
                result = process(topic, payload);
            } catch (IllegalArgumentException rejection) {
                rejected++;
                log.debug("Dropped simulator message on {}: {}", topic, rejection.getMessage());
                return;
            }
            worldChanged = worldVersion != before;
        }
        if (worldChanged) {
            worldListeners.forEach(Runnable::run);
        }
        result.ifPresent(body -> resultListeners.forEach(listener -> listener.accept(body)));
    }

    private Optional<JsonNode> process(String topic, byte[] payload) {
        if (payload.length == 0) {
            if (WORLD_TOPIC.equals(topic) && world != null) {
                world = null; // the simulator cleared its retained world on a normal shutdown
                worldVersion++;
            }
            return Optional.empty();
        }
        if (payload.length > MAX_PAYLOAD_BYTES) {
            throw new IllegalArgumentException("PAYLOAD_TOO_LARGE " + payload.length + " bytes");
        }
        JsonNode envelope;
        try {
            envelope = objectMapper.readTree(payload);
        } catch (IOException exception) {
            throw new IllegalArgumentException("not JSON");
        }
        String type = envelope == null ? null : envelope.path("type").asText(null);
        JsonNode body = envelope == null ? null : envelope.get("payload");
        if (body == null || !body.isObject()) {
            throw new IllegalArgumentException("payload is not an object");
        }
        switch (topic) {
            case POSE_TOPIC -> {
                require("sim.pose", type);
                if (!body.path("x").isNumber() || !body.path("y").isNumber() || !body.path("yaw").isNumber()) {
                    throw new IllegalArgumentException("sim.pose without x, y, yaw");
                }
                pose = body;
                poseAt = monotonicMillis.getAsLong();
            }
            case WORLD_TOPIC -> {
                require("sim.world", type);
                if (!body.path("revision").isIntegralNumber() || !body.path("track").isObject()) {
                    throw new IllegalArgumentException("sim.world without revision, track");
                }
                // A restarted simulator counts from 1 again: a smaller revision is a new run, not a stale copy.
                world = body;
                worldVersion++;
            }
            case RESULT_TOPIC -> {
                require("sim.command.result", type);
                return Optional.of(body);
            }
            default -> throw new IllegalArgumentException("unknown topic");
        }
        return Optional.empty();
    }

    private static void require(String expected, String type) {
        if (!expected.equals(type)) {
            throw new IllegalArgumentException("type " + type + " on the " + expected + " topic");
        }
    }

    public synchronized SimState snapshot() {
        Long age = pose == null ? null : monotonicMillis.getAsLong() - poseAt;
        return new SimState(age != null && age <= AVAILABLE_MS, pose, world, age);
    }

    public synchronized long worldVersion() {
        return worldVersion;
    }

    public synchronized long rejected() {
        return rejected;
    }
}
