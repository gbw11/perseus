package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.application.AgentMessageHandler.Result;
import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricSampleData;
import com.xenova.backend.agent.domain.MetricSampleData.CpuWindow;
import com.xenova.backend.agent.domain.MetricSampleData.TelemetryObservation;
import com.xenova.backend.agent.domain.MetricValue;
import com.xenova.backend.common.UnsignedDecimal;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

// Ingests Linux and Zephyr telemetry 2.1 metric samples.
@Component
public class VmTelemetryMessageHandler {

    public static final String METRICS_TOPIC = "xenova/v2/vms/+/telemetry/metrics";
    static final String SUPPORTED_SCHEMA_VERSION = "2.1";
    private static final Pattern TOPIC_PATTERN = Pattern.compile("^xenova/v2/vms/([^/]+)/telemetry/metrics$");
    private static final Set<String> RAM_METRICS = Set.of("memory.firmware.ram_region_bytes", "memory.firmware.mapped_bytes",
            "memory.firmware.static_data_bytes", "memory.page_allocator.free_bytes");

    private static final Logger log = LoggerFactory.getLogger(VmTelemetryMessageHandler.class);

    private final ObjectMapper objectMapper;
    private final AgentDataService agentDataService;
    private final VmRecordRepository vmRecords;

    public VmTelemetryMessageHandler(
            ObjectMapper objectMapper, AgentDataService agentDataService, VmRecordRepository vmRecords) {
        this.objectMapper = objectMapper;
        this.agentDataService = agentDataService;
        this.vmRecords = vmRecords;
    }

    public static boolean matches(String topic) {
        return TOPIC_PATTERN.matcher(topic).matches();
    }

    public Result handle(String topic, byte[] payload, Instant receivedAt) {
        try {
            return process(topic, payload, receivedAt);
        } catch (Rejected rejected) {
            log.warn("Rejected VM telemetry on {}: {}", topic, rejected.getMessage());
            return Result.REJECTED;
        } catch (RuntimeException exception) {
            log.error("Failed to store VM telemetry on {}", topic, exception);
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
        if (!"telemetry.metrics".equals(root.path("type").asText(null))) {
            throw new Rejected("type " + root.path("type"));
        }
        if (!topicVmId.equals(root.path("vm_id").asText(null))) {
            throw new Rejected("vm_id mismatch: topic " + topicVmId + ", body " + root.path("vm_id"));
        }
        return storeMetrics(topicVmId, root, receivedAt);
    }

    private Result storeMetrics(String topicVmId, JsonNode root, Instant receivedAt) {
        String messageId = root.path("message_id").asText("");
        JsonNode samples = root.path("payload").path("samples");
        if (messageId.isBlank() || !samples.isArray()) {
            throw new Rejected("missing message_id or payload.samples");
        }

        JsonNode window = root.path("payload").path("window");
        String family = root.path("payload").path("family").asText("");
        String source = metricSource(topicVmId, family);
        CpuWindow cpuWindow = null;
        TelemetryObservation telemetry;
        try {
            telemetry = TelemetryMemoryObservation.parse(root);
        } catch (IllegalArgumentException exception) {
            throw new Rejected(exception.getMessage());
        }
        if (MetricSample.ZEPHYR_TELEMETRY.equals(source) && "cpu".equals(family) && window.has("part_count")) {
            try {
                cpuWindow = ZephyrCpuPage.parse(root, window, samples);
            } catch (IllegalArgumentException exception) {
                throw new Rejected(exception.getMessage());
            }
        }
        MetricSample sample = new MetricSample(
                // message_id is "vm/boot/publisher/metrics/seq", longer than the 36-char sample_id column.
                UUID.nameUUIDFromBytes(messageId.getBytes(StandardCharsets.UTF_8)).toString(),
                topicVmId,
                root.path("boot_id").asText(null),
                source,
                observedAt(root.path("clock").path("sent_at"), receivedAt),
                receivedAt,
                cpuWindow == null ? windowMs(window)
                        : UnsignedDecimal.elapsedMillis(cpuWindow.startUptimeMs(), cpuWindow.endUptimeMs()),
                new MetricSampleData(values(samples, family), null, cpuWindow, telemetry));
        return agentDataService.recordMetrics(sample) ? Result.ACCEPTED : Result.IGNORED;
    }

    private String metricSource(String vmId, String family) {
        return vmRecords.findById(vmId).map(VmRecord::getGuestKind)
                .map(kind -> kind == GuestKind.ZEPHYR ? MetricSample.ZEPHYR_TELEMETRY : MetricSample.LINUX_TELEMETRY)
                // Publisher counters are shared. The other supported 2.1 families are Zephyr's profiles.
                .orElse("publisher".equals(family) ? MetricSample.VM_TELEMETRY : MetricSample.ZEPHYR_TELEMETRY);
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

    private static List<MetricValue> values(JsonNode samples, String family) {
        List<MetricValue> values = new ArrayList<>();
        for (JsonNode node : samples) {
            String name = node.path("name").asText("");
            if (name.isBlank()) {
                throw new Rejected("metric without name");
            }
            String status = node.path("status").asText(null);
            boolean ok = "ok".equals(status);
            String raw = null;
            Double value = null;
            if (ok && "u64_decimal".equals(node.path("encoding").asText(null))) {
                try {
                    raw = UnsignedDecimal.read(node.path("value"), name);
                    // JavaScript/API doubles cannot represent arbitrary uint64 gauges exactly.
                    value = UnsignedDecimal.compare(raw, "9007199254740991") <= 0 ? Double.valueOf(raw) : null;
                } catch (IllegalArgumentException ignored) {
                    // Keep this failed value in the sample rather than turn an invalid uint64 into zero.
                }
            } else if (ok) {
                value = number(node.path("value"));
            }
            String reason = null;
            if (value == null && raw == null) {
                reason = ok ? "parse_error"
                        : node.path("reason").isTextual() ? node.path("reason").asText()
                        : node.path("status").asText("unavailable");
            }
            if (ok && RAM_METRICS.contains(name) && !ramContractMatches(node, family)) {
                // Preserve the malformed observation instead of leaving an older valid RAM sample as the latest.
                // A canonical source value remains diagnostic evidence even when its declared encoding is wrong.
                if (raw == null) {
                    try {
                        raw = UnsignedDecimal.read(node.path("value"), name);
                    } catch (IllegalArgumentException ignored) {
                        // Non-uint64 source content cannot be represented by the exact raw integer field.
                    }
                }
                value = null;
                reason = "ram_contract_mismatch";
            }
            JsonNode sourceRc = node.path("source_rc");
            values.add(new MetricValue(name, value, node.path("unit").asText(null), labels(node.path("labels")),
                    reason, "raw_counter".equals(node.path("kind").asText()) ? raw : null,
                    sourceRc.isIntegralNumber() && sourceRc.canConvertToInt() ? sourceRc.intValue() : null,
                    raw, status));
        }
        return values;
    }

    private static boolean ramContractMatches(JsonNode node, String family) {
        return "memory".equals(family) && "bytes".equals(node.path("unit").asText())
                && "gauge".equals(node.path("kind").asText())
                && "u64_decimal".equals(node.path("encoding").asText())
                && node.path("labels").isObject() && node.path("labels").isEmpty();
    }

    // The numeric API represents boolean gauges as 0/1; decimal strings retain their existing handling.
    private static Double number(JsonNode value) {
        if (value.isBoolean()) {
            return value.booleanValue() ? 1.0 : 0.0;
        }
        if (!value.isNumber() && !value.isTextual()) {
            return null;
        }
        try {
            double number = value.isNumber() ? value.doubleValue() : Double.parseDouble(value.textValue());
            return Double.isFinite(number) ? number : null;
        } catch (NumberFormatException exception) {
            return null;
        }
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
