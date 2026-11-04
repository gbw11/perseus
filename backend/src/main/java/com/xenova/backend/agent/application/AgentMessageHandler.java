package com.xenova.backend.agent.application;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.domain.InventoryReport;
import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricSampleData;
import com.xenova.backend.agent.domain.MetricValue;
import com.xenova.backend.agent.domain.NetworkInterface;
import com.xenova.backend.agent.domain.ProcessCollection;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

// Validates agent MQTT messages against the contract and hands them to storage.
// It never throws: a bad message is logged and dropped so the subscription keeps running.
@Component
public class AgentMessageHandler {

    public static final String TOPIC_PREFIX = "xenova/v1/vms/";

    static final int MAX_PAYLOAD_BYTES = 64 * 1024;
    static final String SUPPORTED_SCHEMA_VERSION = "1.0";
    private static final Pattern TOPIC = Pattern.compile("^xenova/v1/vms/([^/]+)/(heartbeat|inventory|metrics)$");
    private static final Set<String> METRIC_UNITS = Set.of("percent", "bytes");
    private static final Set<String> PROCESS_STATUSES = Set.of("first_sample", "ok", "partial", "read_failed", "disabled");
    private static final Pattern PROCESS_ERROR = Pattern.compile("[A-Za-z_][A-Za-z0-9_]{0,63}(\\([A-Z][A-Z0-9_]{0,31}\\))?");
    private static final String GUEST_AGENT_SOURCE = "guest_agent";

    private static final Logger log = LoggerFactory.getLogger(AgentMessageHandler.class);

    private final ObjectMapper objectMapper;
    private final AgentDataService agentDataService;
    private final VmRecordRepository vmRecords;

    public AgentMessageHandler(ObjectMapper objectMapper, AgentDataService agentDataService,
            VmRecordRepository vmRecords) {
        this.objectMapper = objectMapper;
        this.agentDataService = agentDataService;
        this.vmRecords = vmRecords;
    }

    public Result handle(String topic, byte[] payload, Instant receivedAt) {
        try {
            return process(topic, payload, receivedAt);
        } catch (Rejected rejected) {
            log.warn("Rejected agent message on {}: {} {}", topic, rejected.code, rejected.getMessage());
            return Result.REJECTED;
        } catch (RuntimeException exception) {
            log.error("Failed to store agent message on {}", topic, exception);
            return Result.REJECTED;
        }
    }

    private Result process(String topic, byte[] payload, Instant receivedAt) {
        Matcher matcher = TOPIC.matcher(topic);
        if (!matcher.matches()) {
            throw new Rejected("PAYLOAD_INVALID", "unknown topic");
        }
        String topicVmId = matcher.group(1);
        String kind = matcher.group(2);
        if (payload.length > MAX_PAYLOAD_BYTES) {
            throw new Rejected("PAYLOAD_TOO_LARGE", payload.length + " bytes");
        }

        AgentEnvelope envelope = read(payload, AgentEnvelope.class);
        boolean zephyrNetwork = "metrics".equals(kind) && envelope.payload() != null
                && envelope.payload().has("observed_uptime_ms");
        validateEnvelope(envelope, topicVmId, "agent." + kind, zephyrNetwork);

        boolean stored = switch (kind) {
            case "heartbeat" -> agentDataService.recordHeartbeat(
                    topicVmId, envelope.bootId(), envelope.agentInstanceId(), envelope.seq(), envelope.sentAt(),
                    receivedAt);
            case "inventory" -> agentDataService.recordInventory(
                    topicVmId, toInventory(envelope), receivedAt);
            case "metrics" -> zephyrNetwork
                    ? storeZephyrNetwork(topicVmId, envelope, receivedAt)
                    : storeMetrics(topicVmId, envelope, receivedAt);
            default -> throw new Rejected("PAYLOAD_INVALID", "unknown kind " + kind);
        };
        return stored ? Result.ACCEPTED : Result.IGNORED;
    }

    private void validateEnvelope(AgentEnvelope envelope, String topicVmId, String expectedType,
            boolean zephyrNetwork) {
        if (!SUPPORTED_SCHEMA_VERSION.equals(envelope.schemaVersion())) {
            throw new Rejected("SCHEMA_VERSION_UNSUPPORTED", String.valueOf(envelope.schemaVersion()));
        }
        if (!topicVmId.equals(envelope.vmId())) {
            throw new Rejected("VM_ID_MISMATCH", "topic " + topicVmId + ", body " + envelope.vmId());
        }
        if (!expectedType.equals(envelope.type())) {
            throw new Rejected("PAYLOAD_INVALID", "type " + envelope.type());
        }
        if (isBlank(envelope.messageId()) || isBlank(envelope.bootId()) || isBlank(envelope.agentInstanceId())
                || envelope.seq() == null || envelope.seq() < 1 || (!zephyrNetwork && envelope.sentAt() == null)
                || envelope.payload() == null || !envelope.payload().isObject()) {
            throw new Rejected("PAYLOAD_INVALID", "missing envelope field");
        }
    }

    private boolean storeZephyrNetwork(String vmId, AgentEnvelope envelope, Instant receivedAt) {
        MetricSampleData data;
        try {
            data = ZephyrNetworkMetrics.parse(envelope);
        } catch (IllegalArgumentException exception) {
            throw new Rejected("PAYLOAD_INVALID", exception.getMessage());
        }
        boolean registeredZephyr = vmRecords.findById(vmId).filter(VmRecord::isRegistered)
                .map(vm -> vm.getGuestKind() == GuestKind.ZEPHYR).orElse(false);
        if (!registeredZephyr) {
            throw new Rejected("PAYLOAD_INVALID", "Zephyr network metrics require a registered Zephyr VM");
        }
        Instant observedAt = envelope.sentAt() == null ? receivedAt : envelope.sentAt();
        return agentDataService.recordMetrics(new MetricSample(envelope.messageId(), vmId,
                envelope.bootId(), MetricSample.ZEPHYR_TELEMETRY, observedAt, receivedAt, null, data));
    }

    private InventoryReport toInventory(AgentEnvelope envelope) {
        AgentPayloads.Inventory inventory = convert(envelope, AgentPayloads.Inventory.class);
        if (inventory.os() == null || isBlank(inventory.os().id())
                || isBlank(inventory.kernelRelease()) || isBlank(inventory.hostname())) {
            throw new Rejected("PAYLOAD_INVALID", "missing inventory field");
        }
        List<NetworkInterface> interfaces = inventory.interfaces() == null ? List.of() : inventory.interfaces().stream()
                .map(nic -> new NetworkInterface(nic.name(), nic.mac(), nic.addresses() == null ? List.of()
                        : nic.addresses().stream().map(AgentPayloads.Address::address).toList()))
                .toList();
        return new InventoryReport(
                envelope.bootId(),
                inventory.os().id(),
                inventory.os().versionId(),
                inventory.os().prettyName(),
                inventory.kernelRelease(),
                inventory.hostname(),
                interfaces,
                inventory.agentVersion(),
                envelope.sentAt());
    }

    private boolean storeMetrics(String vmId, AgentEnvelope envelope, Instant receivedAt) {
        AgentPayloads.Metrics metrics = convert(envelope, AgentPayloads.Metrics.class);
        List<MetricValue> values = metrics.values() == null ? List.of() : metrics.values();
        for (MetricValue value : values) {
            if (isBlank(value.name()) || !METRIC_UNITS.contains(value.unit())) {
                throw new Rejected("METRIC_UNIT_MISMATCH", value.name() + " " + value.unit());
            }
        }
        MetricSample sample = new MetricSample(
                envelope.messageId(),
                vmId,
                envelope.bootId(),
                GUEST_AGENT_SOURCE,
                envelope.sentAt(),
                receivedAt,
                metrics.windowMs(),
                values);
        return agentDataService.recordMetrics(sample, metrics.processes() == null ? List.of() : metrics.processes(),
                processCollection(metrics.processCollection()));
    }

    // A malformed optional diagnostic leaves the metric and process rows usable for older agents and consumers.
    private static ProcessCollection processCollection(JsonNode node) {
        if (node == null || !node.isObject()) {
            return null;
        }
        String status = node.path("status").asText("");
        JsonNode discovered = node.path("discovered");
        JsonNode readOk = node.path("read_ok");
        JsonNode vanished = node.path("vanished");
        JsonNode failed = node.path("failed");
        JsonNode truncated = node.path("truncated");
        JsonNode errorNode = node.path("error");
        String error = errorNode.textValue();
        if (!PROCESS_STATUSES.contains(status) || !truncated.isBoolean()
                || List.of(discovered, readOk, vanished, failed).stream().anyMatch(
                        count -> !count.isIntegralNumber() || !count.canConvertToInt() || count.intValue() < 0)
                || (!errorNode.isNull() && !errorNode.isMissingNode()
                        && (error == null || !PROCESS_ERROR.matcher(error).matches()))) {
            return null;
        }
        return new ProcessCollection(status, discovered.intValue(), readOk.intValue(), vanished.intValue(),
                failed.intValue(), truncated.booleanValue(), error);
    }

    private <T> T read(byte[] payload, Class<T> type) {
        try {
            return objectMapper.readValue(payload, type);
        } catch (java.io.IOException exception) {
            throw new Rejected("PAYLOAD_INVALID", "malformed JSON");
        }
    }

    private <T> T convert(AgentEnvelope envelope, Class<T> type) {
        try {
            return objectMapper.treeToValue(envelope.payload(), type);
        } catch (JsonProcessingException | IllegalArgumentException exception) {
            throw new Rejected("PAYLOAD_INVALID", "malformed payload");
        }
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    public enum Result {
        ACCEPTED,
        // Valid but not newer than what is stored (duplicate or delayed delivery).
        IGNORED,
        REJECTED
    }

    private static final class Rejected extends RuntimeException {

        private final String code;

        Rejected(String code, String detail) {
            super(detail);
            this.code = code;
        }
    }
}
