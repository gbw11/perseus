package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.application.AgentMessageHandler.Result;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore.Heartbeat;
import java.io.IOException;
import java.time.Instant;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

// Keeps the v2 telemetry heartbeat (Telemetry contract 2.1) of Linux agents and Zephyr Publishers: whether the
// Publisher's collect loop is running or degraded and how long ago it last collected successfully.
// Like the other agent handlers it never throws: a bad message is logged and dropped.
@Component
public class TelemetryHeartbeatMessageHandler {

    public static final String TOPIC = "xenova/v2/vms/+/telemetry/heartbeat";

    private static final Pattern TOPIC_PATTERN = Pattern.compile("^xenova/v2/vms/([^/]+)/telemetry/heartbeat$");
    private static final String TYPE = "telemetry.heartbeat";
    private static final Set<String> STATES = Set.of("running", "degraded", "stopping");

    private static final Logger log = LoggerFactory.getLogger(TelemetryHeartbeatMessageHandler.class);

    private final ObjectMapper objectMapper;
    private final TelemetryHeartbeatStore store;

    public TelemetryHeartbeatMessageHandler(ObjectMapper objectMapper, TelemetryHeartbeatStore store) {
        this.objectMapper = objectMapper;
        this.store = store;
    }

    public static boolean matches(String topic) {
        return TOPIC_PATTERN.matcher(topic).matches();
    }

    public Result handle(String topic, byte[] payload, boolean retained, Instant receivedAt) {
        try {
            return process(topic, payload, retained, receivedAt);
        } catch (Rejected rejected) {
            log.warn("Rejected telemetry heartbeat on {}: {}", topic, rejected.getMessage());
            return Result.REJECTED;
        } catch (RuntimeException exception) {
            log.error("Failed to keep telemetry heartbeat on {}", topic, exception);
            return Result.REJECTED;
        }
    }

    private Result process(String topic, byte[] payload, boolean retained, Instant receivedAt) {
        Matcher matcher = TOPIC_PATTERN.matcher(topic);
        if (!matcher.matches()) {
            throw new Rejected("unknown topic");
        }
        String topicVmId = matcher.group(1);
        if (payload.length > AgentMessageHandler.MAX_PAYLOAD_BYTES) {
            throw new Rejected(payload.length + " bytes");
        }
        JsonNode root = read(payload);
        if (!ZephyrTelemetryMessageHandler.SUPPORTED_SCHEMA_VERSION.equals(root.path("schema_version").asText(null))) {
            throw new Rejected("schema_version " + root.path("schema_version"));
        }
        if (!TYPE.equals(root.path("type").asText(null))) {
            throw new Rejected("type " + root.path("type"));
        }
        if (!topicVmId.equals(root.path("vm_id").asText(null))) {
            throw new Rejected("vm_id mismatch: topic " + topicVmId + ", body " + root.path("vm_id"));
        }
        String bootId = text(root.path("boot_id"));
        String instanceId = text(root.path("publisher_instance_id"));
        Long seq = decimal(root.path("seq"));
        if (bootId == null || instanceId == null || seq == null) {
            throw new Rejected("missing boot_id, publisher_instance_id or seq");
        }
        JsonNode body = root.path("payload");
        String state = body.path("publisher_state").asText(null);
        if (!STATES.contains(state)) {
            throw new Rejected("publisher_state " + body.path("publisher_state"));
        }

        Heartbeat heartbeat = new Heartbeat(
                topicVmId,
                bootId,
                instanceId,
                seq,
                state,
                lastSuccessAgeMs(decimal(root.path("clock").path("sent_uptime_ms")),
                        decimal(body.path("last_collection_success_uptime_ms"))),
                decimal(body.path("collection_failures_total")),
                decimal(body.path("publish_submit_failures_total")),
                decimal(body.path("connection_attempts_total")),
                receivedAt);
        return store.offer(heartbeat, retained) ? Result.ACCEPTED : Result.IGNORED;
    }

    // Both are the Publisher's own uptime, so their difference is meaningful without a trusted UTC clock.
    private static Long lastSuccessAgeMs(Long sentUptimeMs, Long lastSuccessUptimeMs) {
        if (sentUptimeMs == null || lastSuccessUptimeMs == null || lastSuccessUptimeMs > sentUptimeMs) {
            return null;
        }
        return sentUptimeMs - lastSuccessUptimeMs;
    }

    // u64 arrives as a decimal string (contract §5); a plain JSON number is accepted too.
    private static Long decimal(JsonNode value) {
        try {
            if (value.isTextual()) {
                return Long.parseUnsignedLong(value.asText());
            }
            return value.isIntegralNumber() && value.asLong() >= 0 ? value.asLong() : null;
        } catch (NumberFormatException exception) {
            return null;
        }
    }

    private static String text(JsonNode value) {
        return value.isTextual() && !value.asText().isBlank() ? value.asText() : null;
    }

    private JsonNode read(byte[] payload) {
        try {
            JsonNode root = objectMapper.readTree(payload);
            if (root == null || !root.isObject()) {
                throw new Rejected("not a JSON object");
            }
            return root;
        } catch (IOException exception) {
            throw new Rejected("malformed JSON");
        }
    }

    private static final class Rejected extends RuntimeException {

        Rejected(String detail) {
            super(detail);
        }
    }
}
