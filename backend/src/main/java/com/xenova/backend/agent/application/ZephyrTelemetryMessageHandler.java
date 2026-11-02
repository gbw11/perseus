package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.application.AgentMessageHandler.Result;
import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricValue;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

// Stores Zephyr Telemetry Publisher metrics (contract 2.1, platform/guests/zephyr/telemetry/contract) as metric
// samples, so Zephyr VMs are read through the same metrics API as Linux agents.
// One message carries one family (cpu, memory, memory_regions, ...); each becomes its own sample.
// Like AgentMessageHandler it never throws: a bad message is logged and dropped.
@Component
public class ZephyrTelemetryMessageHandler {

    public static final String TOPIC = "xenova/v2/vms/+/telemetry/metrics";

    static final String SUPPORTED_SCHEMA_VERSION = "2.1";
    static final String SOURCE = "zephyr_telemetry";
    private static final Pattern TOPIC_PATTERN = Pattern.compile("^xenova/v2/vms/([^/]+)/telemetry/metrics$");
    private static final String TYPE = "telemetry.metrics";

    private static final Logger log = LoggerFactory.getLogger(ZephyrTelemetryMessageHandler.class);

    private final ObjectMapper objectMapper;
    private final AgentDataService agentDataService;

    public ZephyrTelemetryMessageHandler(ObjectMapper objectMapper, AgentDataService agentDataService) {
        this.objectMapper = objectMapper;
        this.agentDataService = agentDataService;
    }

    public static boolean matches(String topic) {
        return TOPIC_PATTERN.matcher(topic).matches();
    }

    public Result handle(String topic, byte[] payload, Instant receivedAt) {
        try {
            return process(topic, payload, receivedAt);
        } catch (Rejected rejected) {
            log.warn("Rejected Zephyr telemetry on {}: {}", topic, rejected.getMessage());
            return Result.REJECTED;
        } catch (RuntimeException exception) {
            log.error("Failed to store Zephyr telemetry on {}", topic, exception);
            return Result.REJECTED;
        }
    }

    private Result process(String topic, byte[] payload, Instant receivedAt) {
        Matcher matcher = TOPIC_PATTERN.matcher(topic);
        if (!matcher.matches()) {
            throw new Rejected("unknown topic");
        }
        String topicVmId = matcher.group(1);
        if (payload.length > AgentMessageHandler.MAX_PAYLOAD_BYTES) {
            throw new Rejected(payload.length + " bytes");
        }
        JsonNode root = read(payload);
        if (!SUPPORTED_SCHEMA_VERSION.equals(root.path("schema_version").asText(null))) {
            throw new Rejected("schema_version " + root.path("schema_version"));
        }
        if (!TYPE.equals(root.path("type").asText(null))) {
            throw new Rejected("type " + root.path("type"));
        }
        if (!topicVmId.equals(root.path("vm_id").asText(null))) {
            throw new Rejected("vm_id mismatch: topic " + topicVmId + ", body " + root.path("vm_id"));
        }
        String messageId = root.path("message_id").asText("");
        JsonNode samples = root.path("payload").path("samples");
        if (messageId.isBlank() || !samples.isArray()) {
            throw new Rejected("missing message_id or payload.samples");
        }

        MetricSample sample = new MetricSample(
                // message_id is "vm/boot/publisher/metrics/seq", longer than the 36-char sample_id column.
                UUID.nameUUIDFromBytes(messageId.getBytes(StandardCharsets.UTF_8)).toString(),
                topicVmId,
                root.path("boot_id").asText(null),
                SOURCE,
                observedAt(root.path("clock").path("sent_at"), receivedAt),
                receivedAt,
                windowMs(root.path("payload").path("window")),
                values(samples));
        return agentDataService.recordMetrics(sample) ? Result.ACCEPTED : Result.IGNORED;
    }

    // Without trusted UTC (sent_at null) the Publisher only knows its own uptime, so arrival time stands in.
    private static Instant observedAt(JsonNode sentAt, Instant receivedAt) {
        if (!sentAt.isTextual()) {
            return receivedAt;
        }
        try {
            return Instant.parse(sentAt.asText());
        } catch (DateTimeParseException exception) {
            return receivedAt;
        }
    }

    private static Integer windowMs(JsonNode window) {
        Long start = decimal(window.path("start_uptime_ms"));
        Long end = decimal(window.path("end_uptime_ms"));
        if (start == null || end == null || end < start || end - start > Integer.MAX_VALUE) {
            return null;
        }
        return (int) (end - start);
    }

    private static List<MetricValue> values(JsonNode samples) {
        List<MetricValue> values = new ArrayList<>();
        for (JsonNode node : samples) {
            String name = node.path("name").asText("");
            if (name.isBlank()) {
                throw new Rejected("metric without name");
            }
            boolean ok = "ok".equals(node.path("status").asText(null));
            Double value = ok ? number(node.path("value")) : null;
            String reason = value != null ? null
                    : node.path("reason").isTextual() ? node.path("reason").asText()
                    : node.path("status").asText("unavailable");
            values.add(new MetricValue(name, value, node.path("unit").asText(null), labels(node.path("labels")),
                    reason));
        }
        return values;
    }

    // u64/i64 arrive as decimal strings, float64 as JSON numbers (contract §5).
    private static Double number(JsonNode value) {
        if (value.isNumber()) {
            return value.asDouble();
        }
        if (value.isTextual()) {
            try {
                return Double.valueOf(value.asText());
            } catch (NumberFormatException exception) {
                return null;
            }
        }
        return null;
    }

    private static Long decimal(JsonNode value) {
        try {
            return value.isTextual() ? Long.valueOf(value.asText()) : value.isNumber() ? value.asLong() : null;
        } catch (NumberFormatException exception) {
            return null;
        }
    }

    private static Map<String, String> labels(JsonNode labels) {
        Map<String, String> result = new LinkedHashMap<>();
        labels.properties().forEach(entry -> result.put(entry.getKey(), entry.getValue().asText()));
        return result;
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
