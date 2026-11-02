package com.xenova.backend.drive.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.time.Clock;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;
import java.util.function.LongSupplier;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

// Keeps the latest drive picture from the MQTT topics of contract §5·§6·§6.1. Called on the MQTT callback
// thread at ~120 messages/s, so it only parses and updates memory; storage and pushes happen elsewhere.
// A bad message is counted and dropped, never thrown, so the subscription keeps running.
@Component
public class DriveStateTracker {

    public static final String PREFIX = "xenova/v1/";
    public static final String CONTROL_TOPIC = PREFIX + "vehicle/control";
    public static final String STATUS_TOPIC = PREFIX + "vehicle/status";
    public static final String APP_EVENTS_FILTER = PREFIX + "apps/+/events";

    /** The gateway's freshness rule (contract §5.1): a command older than this has stopped. */
    static final long COMMAND_FRESH_MS = 100;
    /** vehicle.status is 20 Hz; twenty missed reports make the telemetry stale. */
    static final long VEHICLE_FRESH_MS = 1000;
    /** Perception is 15 Hz; three missed frames mean the input is no longer valid. */
    static final long PERCEPTION_VALID_MS = 200;
    static final long RATE_WINDOW_MS = 1000;
    static final int MAX_PAYLOAD_BYTES = 64 * 1024;
    static final String SCHEMA_VERSION = "1.0";
    /** Default countdown when drive.resume.accepted carries no switch_in_ms. */
    static final long RESUME_SWITCH_MS = 3000;
    /** A countdown the gateway never finished (no mode change arrived) disappears this long after its end. */
    static final long RESUME_GRACE_MS = 2000;

    private static final Pattern APP_EVENTS = Pattern.compile("^xenova/v1/apps/([^/+#\\s]+)/events$");
    private static final Logger log = LoggerFactory.getLogger(DriveStateTracker.class);

    private final ObjectMapper objectMapper;
    private final Clock clock;
    private final LongSupplier monotonicMillis;

    private long version;
    private ConnectionState mqttConnection = ConnectionState.UNKNOWN;
    private long mqttGeneration;
    private Stamped control;
    private Stamped vehicle;
    private Stamped perception;
    private Stamped resume;
    private final Deque<Long> perceptionTimes = new ArrayDeque<>();
    private final Map<String, AppTrack> apps = new TreeMap<>();
    private final Map<String, Counter> stats = new TreeMap<>();
    private final Map<String, Run> lastSeq = new HashMap<>();
    private long rejected;

    @Autowired
    public DriveStateTracker(ObjectMapper objectMapper, Clock clock) {
        this(objectMapper, clock, () -> System.nanoTime() / 1_000_000);
    }

    DriveStateTracker(ObjectMapper objectMapper, Clock clock, LongSupplier monotonicMillis) {
        this.objectMapper = objectMapper;
        this.clock = clock;
        this.monotonicMillis = monotonicMillis;
    }

    // ---- intake -------------------------------------------------------------------------------------

    /** Returns a notice to record for drive.mode.changed / drive.fault.detected / drive.resume.* events. */
    public synchronized Optional<DriveNotice> accept(String topic, byte[] payload) {
        long now = monotonicMillis.getAsLong();
        try {
            return process(topic, payload, now);
        } catch (Rejected rejection) {
            rejected++;
            log.debug("Dropped drive message on {}: {} {}", topic, rejection.code, rejection.getMessage());
            return Optional.empty();
        }
    }

    private Optional<DriveNotice> process(String topic, byte[] payload, long now) {
        if (payload.length == 0) {
            return Optional.empty(); // a retained message being cleared
        }
        if (payload.length > MAX_PAYLOAD_BYTES) {
            throw new Rejected("PAYLOAD_TOO_LARGE", payload.length + " bytes");
        }
        Matcher appTopic = APP_EVENTS.matcher(topic);
        String topicAppId = appTopic.matches() ? appTopic.group(1) : null;
        if (topicAppId == null && !CONTROL_TOPIC.equals(topic) && !STATUS_TOPIC.equals(topic)) {
            throw new Rejected("PAYLOAD_INVALID", "unknown topic");
        }
        JsonNode envelope = read(payload);
        validate(envelope, topicAppId);
        String type = envelope.path("type").asText();
        count(topic, type, now);
        if (!acceptSeq(topic, envelope)) {
            return Optional.empty(); // duplicate or reordered (contract §2)
        }
        JsonNode body = envelope.path("payload");
        String publisher = text(envelope, "vm_id") != null ? text(envelope, "vm_id") : text(envelope, "source_id");

        if (CONTROL_TOPIC.equals(topic)) {
            control = new Stamped(body, now, publisher, mqttGeneration);
            if (!"AUTO_READY".equals(text(body, "mode"))) {
                resume = null;
            }
        } else if (STATUS_TOPIC.equals(topic)) {
            vehicle = new Stamped(body, now, publisher, mqttGeneration);
        } else {
            return onAppEvent(topicAppId, type, body, publisher, now);
        }
        version++;
        return Optional.empty();
    }

    private Optional<DriveNotice> onAppEvent(String appId, String type, JsonNode body, String publisher, long now) {
        switch (type) {
            case "drive.command" -> {
                AppTrack app = app(appId);
                app.vmId = publisher;
                app.command = new Stamped(body, now, publisher, mqttGeneration);
                window(app.commandTimes, now).addLast(now);
            }
            case "app.heartbeat" -> {
                AppTrack app = app(appId);
                app.vmId = publisher;
                app.heartbeat = new Stamped(body, now, publisher, mqttGeneration);
            }
            case "perception.result" -> {
                perception = new Stamped(body, now, publisher, mqttGeneration);
                window(perceptionTimes, now).addLast(now);
            }
            case "drive.resume.accepted" -> {
                resume = new Stamped(body, now, publisher, mqttGeneration);
                version++;
                return Optional.of(new DriveNotice(type, publisher, body));
            }
            case "drive.resume.cancelled", "drive.mode.changed" -> {
                resume = null;
                version++;
                return Optional.of(new DriveNotice(type, publisher, body));
            }
            case "drive.fault.detected" -> {
                version++;
                return Optional.of(new DriveNotice(type, publisher, body));
            }
            default -> {
                // Other app events (perception.event, message.posted ...) are only counted.
            }
        }
        version++;
        return Optional.empty();
    }

    // Same envelope rules as the Python client and AgentMessageHandler (contract §2).
    private void validate(JsonNode envelope, String topicAppId) {
        if (!envelope.isObject()) {
            throw new Rejected("PAYLOAD_INVALID", "envelope is not an object");
        }
        if (!SCHEMA_VERSION.equals(text(envelope, "schema_version"))) {
            throw new Rejected("SCHEMA_VERSION_UNSUPPORTED", String.valueOf(text(envelope, "schema_version")));
        }
        for (String field : new String[] {"message_id", "type", "sent_at", "boot_id", "agent_instance_id"}) {
            if (text(envelope, field) == null) {
                throw new Rejected("PAYLOAD_INVALID", "missing " + field);
            }
        }
        JsonNode seq = envelope.path("seq");
        if (!seq.canConvertToLong() || !seq.isIntegralNumber() || seq.asLong() < 1) {
            throw new Rejected("PAYLOAD_INVALID", "seq " + seq);
        }
        if (!envelope.path("payload").isObject()) {
            throw new Rejected("PAYLOAD_INVALID", "payload is not an object");
        }
        if (text(envelope, "vm_id") == null && text(envelope, "source_id") == null) {
            throw new Rejected("PAYLOAD_INVALID", "neither vm_id nor source_id");
        }
        if (topicAppId != null && !topicAppId.equals(text(envelope, "app_id"))) {
            throw new Rejected("APP_ID_MISMATCH", "topic " + topicAppId + ", body " + text(envelope, "app_id"));
        }
    }

    // Per (topic, publisher): a restart (new boot_id / agent_instance_id) resets the baseline.
    private boolean acceptSeq(String topic, JsonNode envelope) {
        String publisher = text(envelope, "vm_id") != null ? text(envelope, "vm_id") : text(envelope, "source_id");
        String key = topic + "|" + publisher;
        String run = text(envelope, "boot_id") + "|" + text(envelope, "agent_instance_id");
        long seq = envelope.path("seq").asLong();
        Run previous = lastSeq.get(key);
        if (previous != null && previous.run.equals(run) && seq <= previous.seq) {
            return false;
        }
        lastSeq.put(key, new Run(run, seq));
        return true;
    }

    // ---- views --------------------------------------------------------------------------------------

    public synchronized long version() {
        return version;
    }

    public synchronized DriveState snapshot() {
        long now = monotonicMillis.getAsLong();
        Map<String, DriveState.App> appViews = new LinkedHashMap<>();
        apps.forEach((appId, app) -> appViews.put(appId, appView(app, now)));
        return new DriveState(
                version, clock.instant(), transportSource(), controlView(now), vehicleView(now), appViews,
                perceptionView(now),
                resumeView(now));
    }

    /** MQTT lifecycle callbacks change the state version so WebSocket clients receive the boundary immediately. */
    public synchronized void mqttConnected() {
        if (mqttConnection != ConnectionState.CONNECTED) {
            mqttGeneration++;
        }
        setConnection(ConnectionState.CONNECTED);
    }

    public synchronized void mqttDisconnected() {
        setConnection(ConnectionState.DISCONNECTED);
    }

    private void setConnection(ConnectionState next) {
        if (mqttConnection != next) {
            mqttConnection = next;
            version++;
        }
    }

    public synchronized MessagingStats stats() {
        long now = monotonicMillis.getAsLong();
        Map<String, MessagingStats.Entry> entries = new LinkedHashMap<>();
        stats.forEach((key, counter) -> entries.put(key, new MessagingStats.Entry(
                counter.topic, counter.type, counter.total, window(counter.times, now).size(), now - counter.last)));
        return new MessagingStats(clock.instant(), rejected, entries.values().stream().toList());
    }

    private DriveState.Control controlView(long now) {
        if (control == null) {
            return null;
        }
        JsonNode c = control.body;
        return new DriveState.Control(
                text(c, "mode"), longOrNull(c, "generation"), text(c, "active_app"), text(c, "active_vm_id"),
                text(c, "reason"), now - control.at, source(control, now, null));
    }

    private DriveState.Vehicle vehicleView(long now) {
        if (vehicle == null) {
            return null;
        }
        JsonNode v = vehicle.body;
        JsonNode flags = v.path("flags");
        return new DriveState.Vehicle(
                longOrNull(v, "applied_generation"), doubleOrNull(v, "v_mps"), doubleOrNull(v, "steer_rad"),
                doubleOrNull(v, "batt_v"),
                new DriveState.Flags(
                        flags.path("estop").asBoolean(false), flags.path("watchdog").asBoolean(false),
                        flags.path("obstacle").asBoolean(false)),
                now - vehicle.at, source(vehicle, now, VEHICLE_FRESH_MS));
    }

    private DriveState.App appView(AppTrack app, long now) {
        DriveState.Command command = null;
        Long commandAgo = null;
        Boolean fresh = null;
        DriveState.CommandDirection direction = null;
        if (app.command != null) {
            JsonNode c = app.command.body;
            command = new DriveState.Command(
                    longOrNull(c, "generation"), doubleOrNull(c, "v_mps"), doubleOrNull(c, "steer_rad"));
            commandAgo = now - app.command.at;
            fresh = mqttConnection == ConnectionState.CONNECTED
                    && app.command.mqttGeneration == mqttGeneration
                    && commandAgo <= COMMAND_FRESH_MS;
            direction = direction(command, fresh);
        }
        return new DriveState.App(
                app.vmId, fresh, commandAgo, window(app.commandTimes, now).size(), command, direction,
                app.heartbeat == null ? null : text(app.heartbeat.body, "state"),
                app.heartbeat == null ? null : now - app.heartbeat.at);
    }

    private DriveState.CommandDirection direction(DriveState.Command command, boolean fresh) {
        if (!fresh) {
            return new DriveState.CommandDirection(
                    DriveState.Longitudinal.UNKNOWN, DriveState.Lateral.UNKNOWN);
        }
        Double velocity = command.vMps();
        Double steering = command.steerRad();
        DriveState.Longitudinal longitudinal = velocity == null ? DriveState.Longitudinal.UNKNOWN
                : velocity > 0 ? DriveState.Longitudinal.FORWARD
                : velocity < 0 ? DriveState.Longitudinal.REVERSE : DriveState.Longitudinal.STOPPED;
        // MQTT contract §7: positive steer is left and negative steer is right.
        DriveState.Lateral lateral = steering == null ? DriveState.Lateral.UNKNOWN
                : steering > 0 ? DriveState.Lateral.LEFT
                : steering < 0 ? DriveState.Lateral.RIGHT : DriveState.Lateral.STRAIGHT;
        return new DriveState.CommandDirection(longitudinal, lateral);
    }

    private DriveState.Source transportSource() {
        return new DriveState.Source(switch (mqttConnection) {
            case CONNECTED -> DriveState.SourceStatus.FRESH;
            case DISCONNECTED -> DriveState.SourceStatus.DISCONNECTED;
            case UNKNOWN -> DriveState.SourceStatus.UNKNOWN;
        }, null);
    }

    private DriveState.Source source(Stamped value, long now, Long staleAfterMs) {
        Long age = value == null ? null : now - value.at;
        if (mqttConnection == ConnectionState.DISCONNECTED) {
            return new DriveState.Source(DriveState.SourceStatus.DISCONNECTED, age);
        }
        if (mqttConnection == ConnectionState.UNKNOWN) {
            return new DriveState.Source(DriveState.SourceStatus.UNKNOWN, age);
        }
        if (value == null) {
            return new DriveState.Source(DriveState.SourceStatus.UNKNOWN, null);
        }
        if (value.mqttGeneration != mqttGeneration) {
            return new DriveState.Source(DriveState.SourceStatus.UNKNOWN, age);
        }
        if (staleAfterMs != null && age > staleAfterMs) {
            return new DriveState.Source(DriveState.SourceStatus.STALE, age);
        }
        return new DriveState.Source(DriveState.SourceStatus.FRESH, age);
    }

    private DriveState.Perception perceptionView(long now) {
        if (perception == null) {
            return null;
        }
        JsonNode p = perception.body;
        long ago = now - perception.at;
        return new DriveState.Perception(
                mqttConnection == ConnectionState.CONNECTED
                        && perception.mqttGeneration == mqttGeneration
                        && ago <= PERCEPTION_VALID_MS,
                ago, window(perceptionTimes, now).size(),
                doubleOrNull(p, "lane_offset_m"), doubleOrNull(p, "heading_err_rad"),
                p.path("obstacles").isArray() ? p.path("obstacles").size() : 0, p.path("stop").asBoolean(false));
    }

    private DriveState.Resume resumeView(long now) {
        if (resume == null) {
            return null;
        }
        if (mqttConnection != ConnectionState.CONNECTED || resume.mqttGeneration != mqttGeneration) {
            return null;
        }
        Long switchIn = longOrNull(resume.body, "switch_in_ms");
        long total = switchIn == null || switchIn < 0 ? RESUME_SWITCH_MS : switchIn;
        long ago = now - resume.at;
        if (ago > total + RESUME_GRACE_MS) {
            return null;
        }
        return new DriveState.Resume(longOrNull(resume.body, "generation"), total, ago, Math.max(0, total - ago));
    }

    // ---- helpers ------------------------------------------------------------------------------------

    private AppTrack app(String appId) {
        return apps.computeIfAbsent(appId, ignored -> new AppTrack());
    }

    private void count(String topic, String type, long now) {
        Counter counter = stats.computeIfAbsent(topic + "|" + type, ignored -> new Counter(topic, type));
        counter.total++;
        counter.last = now;
        window(counter.times, now).addLast(now);
    }

    private static Deque<Long> window(Deque<Long> times, long now) {
        while (!times.isEmpty() && now - times.peekFirst() >= RATE_WINDOW_MS) {
            times.pollFirst();
        }
        return times;
    }

    private JsonNode read(byte[] payload) {
        try {
            return objectMapper.readTree(payload);
        } catch (IOException exception) {
            throw new Rejected("PAYLOAD_INVALID", "not JSON");
        }
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value != null && value.isTextual() && !value.asText().isBlank() ? value.asText() : null;
    }

    private static Long longOrNull(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value != null && value.isIntegralNumber() ? value.asLong() : null;
    }

    private static Double doubleOrNull(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value != null && value.isNumber() ? value.asDouble() : null;
    }

    private record Stamped(JsonNode body, long at, String publisher, long mqttGeneration) {
    }

    private record Run(String run, long seq) {
    }

    private enum ConnectionState { CONNECTED, DISCONNECTED, UNKNOWN }

    private static final class AppTrack {
        private String vmId;
        private Stamped command;
        private Stamped heartbeat;
        private final Deque<Long> commandTimes = new ArrayDeque<>();
    }

    private static final class Counter {
        private final String topic;
        private final String type;
        private long total;
        private long last;
        private final Deque<Long> times = new ArrayDeque<>();

        private Counter(String topic, String type) {
            this.topic = topic;
            this.type = type;
        }
    }

    private static final class Rejected extends RuntimeException {
        private final String code;

        private Rejected(String code, String message) {
            super(message);
            this.code = code;
        }
    }
}
