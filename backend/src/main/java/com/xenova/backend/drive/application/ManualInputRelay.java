package com.xenova.backend.drive.application;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Clock;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Supplier;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;

// Web keys → MQTT-11 drive.input for the manual VM (MQTT contract §7).
//  - Only while the gateway says MANUAL; otherwise input is dropped (the backend is the filter, §7).
//  - While any key is held, the last keys are re-sent every 50 ms (20 Hz). Releasing all keys sends `keys: []`
//    once. The manual VM treats 200 ms without input as "released", so a lost release is harmless.
// There is one operator at a time (2026-10-20 decision), so a single shared key state is enough.
// The repeat runs on its own thread, not Spring's shared @Scheduled thread: a slow dom0-manager call there
// (VmStateWatcher) stalled the repeat for ~0.5 s and the manual VM stopped the car mid-drive.
@Service
public class ManualInputRelay {

    public static final String INPUT_TOPIC = DriveStateTracker.PREFIX + "apps/manual/input";
    public static final Set<String> KEYS = Set.of("up", "down", "left", "right", "brake");
    static final long REPEAT_MS = 50;
    /** Contract §2: non-VM publishers use source_id. The backend is the dom0-server source. */
    static final String SOURCE_ID = "dom0-server";

    private static final Logger log = LoggerFactory.getLogger(ManualInputRelay.class);

    private final DriveStateTracker tracker;
    // Absent when MQTT is disabled (xenova.mqtt.enabled=false); input is then reported as not connected.
    private final Supplier<DriveInputPublisher> publisher;
    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final String bootId = UUID.randomUUID().toString();
    private final String instanceId = UUID.randomUUID().toString();
    private final ScheduledExecutorService repeater = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "manual-input-repeat");
        thread.setDaemon(true);
        return thread;
    });
    private long seq;
    private List<String> held = List.of();

    @Autowired
    public ManualInputRelay(
            DriveStateTracker tracker, ObjectProvider<DriveInputPublisher> publisher, ObjectMapper objectMapper,
            Clock clock) {
        this(tracker, publisher::getIfAvailable, objectMapper, clock);
    }

    ManualInputRelay(
            DriveStateTracker tracker, Supplier<DriveInputPublisher> publisher, ObjectMapper objectMapper, Clock clock) {
        this.tracker = tracker;
        this.publisher = publisher;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    public enum Result { SENT, RELEASED, NOT_MANUAL, NOT_CONNECTED }

    /** Replaces the held keys. Unknown key names are ignored (contract §7). */
    public synchronized Result update(List<String> keys) {
        Set<String> next = new LinkedHashSet<>();
        for (String key : keys == null ? List.<String>of() : keys) {
            if (KEYS.contains(key)) {
                next.add(key);
            }
        }
        boolean wasHeld = !held.isEmpty();
        held = List.copyOf(next);
        if (!manual()) {
            held = List.of();
            return Result.NOT_MANUAL;
        }
        if (held.isEmpty()) {
            if (!wasHeld) {
                return Result.RELEASED;
            }
            return send(List.of()) ? Result.RELEASED : Result.NOT_CONNECTED;
        }
        return send(held) ? Result.SENT : Result.NOT_CONNECTED;
    }

    /** The operator left (socket closed): release everything. */
    public void release() {
        update(List.of());
    }

    @PostConstruct
    void start() {
        // A task that throws is never run again by the executor, so one bad publish must not end the repeat.
        repeater.scheduleAtFixedRate(() -> {
            try {
                repeat();
            } catch (RuntimeException exception) {
                log.warn("Manual input repeat failed: {}", exception.getMessage());
            }
        }, REPEAT_MS, REPEAT_MS, TimeUnit.MILLISECONDS);
    }

    @PreDestroy
    void stop() {
        repeater.shutdownNow();
    }

    public synchronized void repeat() {
        if (held.isEmpty()) {
            return;
        }
        if (!manual()) {
            held = List.of(); // the gateway took control away; stop repeating
            return;
        }
        send(held);
    }

    public synchronized List<String> held() {
        return held;
    }

    private boolean manual() {
        DriveState.Control control = tracker.snapshot().control();
        return control != null
                && control.source().status() == DriveState.SourceStatus.FRESH
                && "MANUAL".equals(control.mode());
    }

    private boolean send(List<String> keys) {
        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schema_version", DriveStateTracker.SCHEMA_VERSION);
        envelope.put("message_id", UUID.randomUUID().toString());
        envelope.put("source_id", SOURCE_ID);
        envelope.put("boot_id", bootId);
        envelope.put("agent_instance_id", instanceId);
        envelope.put("type", "drive.input");
        envelope.put("seq", ++seq);
        envelope.put("sent_at", DateTimeFormatter.ISO_INSTANT.format(clock.instant().truncatedTo(ChronoUnit.MILLIS)));
        envelope.put("payload", Map.of("keys", keys));
        try {
            DriveInputPublisher target = publisher.get();
            return target != null && target.publish(INPUT_TOPIC, objectMapper.writeValueAsBytes(envelope), 0);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException(exception);
        }
    }
}
