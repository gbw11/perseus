package com.perseus.backend.agent.domain;

import java.util.Map;

// A null value with unavailableReason means the agent could not collect it, never zero load.
public record MetricValue(
        String name,
        Double value,
        String unit,
        Map<String, String> labels,
        String unavailableReason) {

    public MetricValue {
        labels = labels == null ? Map.of() : Map.copyOf(labels);
    }
}
