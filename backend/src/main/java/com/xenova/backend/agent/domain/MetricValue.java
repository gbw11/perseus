package com.xenova.backend.agent.domain;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.Map;

// Numeric projections remain convenient; rawValue preserves exact v2 uint64 gauges and counters.
public record MetricValue(
        String name,
        Double value,
        String unit,
        Map<String, String> labels,
        String unavailableReason,
        @JsonInclude(JsonInclude.Include.NON_NULL) String counterValue,
        @JsonInclude(JsonInclude.Include.NON_NULL) Integer sourceRc,
        @JsonInclude(JsonInclude.Include.NON_NULL) String rawValue,
        @JsonInclude(JsonInclude.Include.NON_NULL) String status) {

    public MetricValue(String name, Double value, String unit, Map<String, String> labels, String unavailableReason) {
        this(name, value, unit, labels, unavailableReason, null, null);
    }

    public MetricValue(String name, Double value, String unit, Map<String, String> labels,
            String unavailableReason, String counterValue, Integer sourceRc) {
        this(name, value, unit, labels, unavailableReason, counterValue, sourceRc, null, null);
    }

    public MetricValue {
        labels = labels == null ? Map.of() : Map.copyOf(labels);
    }
}
