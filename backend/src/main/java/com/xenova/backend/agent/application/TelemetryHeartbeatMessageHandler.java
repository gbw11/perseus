package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.TextNode;
import com.xenova.backend.agent.application.AgentMessageHandler.Result;
import com.xenova.backend.agent.domain.TelemetryHeartbeat.Report;
import com.xenova.backend.common.UnsignedDecimal;
import java.io.IOException;
import java.time.Instant;
import java.util.Set;
import java.util.UUID;
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
    private static final int MAX_PAYLOAD_BYTES = 4096;
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
        if (payload.length > MAX_PAYLOAD_BYTES) {
            throw new Rejected(payload.length + " bytes");
        }
        JsonNode root = read(payload);
        if (!VmTelemetryMessageHandler.SUPPORTED_SCHEMA_VERSION.equals(root.path("schema_version").asText(null))) {
            throw new Rejected("schema_version " + root.path("schema_version"));
        }
        if (!TYPE.equals(root.path("type").asText(null))) {
            throw new Rejected("type " + root.path("type"));
        }
        if (!topicVmId.equals(root.path("vm_id").asText(null))) {
            throw new Rejected("vm_id mismatch: topic " + topicVmId + ", body " + root.path("vm_id"));
        }
        String bootId = root.path("boot_id").asText("");
        String instanceId = root.path("publisher_instance_id").asText("");
        String seq = decimal(root.path("seq"));
        if (!uuid(topicVmId) || !uuid(bootId) || !uuid(instanceId) || seq == null || "0".equals(seq)
                || !String.join("/", topicVmId, bootId, instanceId, "heartbeat", seq)
                        .equals(root.path("message_id").asText())) {
            throw new Rejected("invalid heartbeat identity or sequence");
        }
        JsonNode body = root.path("payload");
        String state = body.path("publisher_state").asText("");
        if (!STATES.contains(state)) {
            throw new Rejected("publisher_state " + body.path("publisher_state"));
        }
        if (body.path("app_health_asserted").asBoolean(false)) {
            throw new Rejected("publisher heartbeat cannot assert app health");
        }

        String sent = decimal(root.path("clock").path("sent_uptime_ms"));
        String lastSuccess = decimal(body.path("last_collection_success_uptime_ms"));
        if (sent != null && lastSuccess != null && UnsignedDecimal.compare(lastSuccess, sent) > 0) {
            lastSuccess = null;
        }
        Report heartbeat = new Report(bootId, instanceId, seq, sent, state, lastSuccess,
                decimal(body.path("collection_failures_total")),
                decimal(body.path("publish_submit_failures_total")),
                decimal(body.path("connection_attempts_total")),
                receivedAt);
        return store.offer(topicVmId, heartbeat, retained) ? Result.ACCEPTED : Result.IGNORED;
    }

    // Preserve the exact u64 and continue accepting the older numeric representation.
    private static String decimal(JsonNode value) {
        if (!value.isTextual() && !value.isIntegralNumber()) {
            return null;
        }
        try {
            return UnsignedDecimal.read(value.isIntegralNumber() ? TextNode.valueOf(value.asText()) : value, "u64");
        } catch (IllegalArgumentException exception) {
            return null;
        }
    }

    private static boolean uuid(String value) {
        try {
            return value.length() == 36 && UUID.fromString(value).toString().equals(value)
                    && !"00000000-0000-0000-0000-000000000000".equals(value);
        } catch (IllegalArgumentException exception) {
            return false;
        }
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
