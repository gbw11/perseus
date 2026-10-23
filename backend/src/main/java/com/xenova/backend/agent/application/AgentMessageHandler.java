package com.xenova.backend.agent.application;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.domain.InventoryReport;
import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricValue;
import com.xenova.backend.agent.domain.NetworkInterface;
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
    private static final String GUEST_AGENT_SOURCE = "guest_agent";

    private static final Logger log = LoggerFactory.getLogger(AgentMessageHandler.class);

    private final ObjectMapper objectMapper;
    private final AgentDataService agentDataService;

    public AgentMessageHandler(ObjectMapper objectMapper, AgentDataService agentDataService) {
        this.objectMapper = objectMapper;
        this.agentDataService = agentDataService;
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
        validateEnvelope(envelope, topicVmId, "agent." + kind);

        boolean stored = switch (kind) {
            case "heartbeat" -> agentDataService.recordHeartbeat(
                    topicVmId, envelope.bootId(), envelope.agentInstanceId(), envelope.seq(), envelope.sentAt(),
                    receivedAt);
            case "inventory" -> agentDataService.recordInventory(
                    topicVmId, toInventory(envelope), receivedAt);
            case "metrics" -> storeMetrics(topicVmId, envelope, receivedAt);
            default -> throw new Rejected("PAYLOAD_INVALID", "unknown kind " + kind);
        };
        return stored ? Result.ACCEPTED : Result.IGNORED;
    }

    private void validateEnvelope(AgentEnvelope envelope, String topicVmId, String expectedType) {
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
                || envelope.seq() == null || envelope.seq() < 1 || envelope.sentAt() == null
                || envelope.payload() == null || !envelope.payload().isObject()) {
            throw new Rejected("PAYLOAD_INVALID", "missing envelope field");
        }
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
        return agentDataService.recordMetrics(sample, metrics.processes() == null ? List.of() : metrics.processes());
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
