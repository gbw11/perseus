package com.xenova.backend.agent.domain;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.Map;

// Gauges use value and exact counters use counterValue. Missing observations carry unavailableReason.
public record MetricValue(
        String name,
        Double value,
        String unit,
        Map<String, String> labels,
        String unavailableReason,
        @JsonInclude(JsonInclude.Include.NON_NULL) String counterValue,
        @JsonInclude(JsonInclude.Include.NON_NULL) Integer sourceRc) {

    public MetricValue(String name, Double value, String unit, Map<String, String> labels, String unavailableReason) {
        this(name, value, unit, labels, unavailableReason, null, null);
    }

    public MetricValue {
        labels = labels == null ? Map.of() : Map.copyOf(labels);
    }
}
