package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.xenova.backend.agent.domain.MetricSampleData;
import com.xenova.backend.agent.domain.MetricSampleData.NetworkObservation;
import com.xenova.backend.agent.domain.MetricValue;
import com.xenova.backend.common.UnsignedDecimal;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** The resource-only Zephyr profile of MQTT-03, using the same envelope and topic as Linux. */
final class ZephyrNetworkMetrics {
    private static final Set<String> NAMES = Set.of("net_rx_bytes_total", "net_tx_bytes_total");

    private ZephyrNetworkMetrics() {
    }

    static MetricSampleData parse(AgentEnvelope envelope) {
        requireUuid(envelope.messageId());
        requireUuid(envelope.bootId());
        requireUuid(envelope.agentInstanceId());
        JsonNode payload = envelope.payload();
        JsonNode uptimeNode = payload.path("observed_uptime_ms");
        JsonNode valuesNode = payload.path("values");
        if (!payload.path("window_ms").isNull() || payload.has("processes") || payload.has("process_collection")
                || !valuesNode.isArray() || valuesNode.size() != NAMES.size()) {
            throw new IllegalArgumentException("invalid Zephyr network sample");
        }
        String uptime = uptimeNode.isNull() ? null : UnsignedDecimal.read(uptimeNode, "observed_uptime_ms");
        MetricValue first = value(valuesNode.get(0), uptime != null);
        MetricValue second = value(valuesNode.get(1), uptime != null);
        if (first.name().equals(second.name()) || !first.labels().equals(second.labels())) {
            throw new IllegalArgumentException("duplicate counter or mismatched interface/epoch");
        }
        return new MetricSampleData(List.of(first, second), new NetworkObservation(envelope.agentInstanceId(), uptime),
                null);
    }

    private static MetricValue value(JsonNode node, boolean observed) {
        String name = node.path("name").asText("");
        JsonNode counter = node.path("value");
        JsonNode reason = node.path("unavailable_reason");
        JsonNode sourceRc = node.path("source_rc");
        JsonNode labels = node.path("labels");
        String iface = labels.path("iface").isTextual() ? labels.path("iface").textValue() : "";
        String epoch = UnsignedDecimal.read(labels.path("counter_epoch"), "counter_epoch");
        if (!NAMES.contains(name) || !"bytes".equals(node.path("unit").asText())
                || iface.isBlank() || iface.length() > 31 || !sourceRc.isIntegralNumber()
                || !sourceRc.canConvertToInt()) {
            throw new IllegalArgumentException("invalid network counter");
        }
        int rc = sourceRc.intValue();
        if (observed ? rc != 0 || !reason.isNull() || "0".equals(epoch)
                : rc >= 0 || !counter.isNull() || !reason.isTextual() || reason.textValue().isBlank()
                || reason.textValue().length() > 128) {
            throw new IllegalArgumentException("invalid network counter status");
        }
        String exact = observed ? UnsignedDecimal.read(counter, name) : null;
        return new MetricValue(name, null, "bytes", Map.of("iface", iface, "counter_epoch", epoch),
                observed ? null : reason.textValue(), exact, rc);
    }

    private static void requireUuid(String value) {
        if (value.length() != 36 || !UUID.fromString(value).toString().equals(value)) {
            throw new IllegalArgumentException("invalid Zephyr sample identity");
        }
    }
}
