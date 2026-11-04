package com.xenova.backend.agent.domain;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.List;

/** Sample-level wire context stored alongside the values in the existing JSON column. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record MetricSampleData(List<MetricValue> values, NetworkObservation network, CpuWindow cpuWindow) {

    public MetricSampleData {
        values = values == null ? List.of() : List.copyOf(values);
    }

    public record NetworkObservation(String publisherInstanceId, String observedUptimeMs) {
    }

    public record CpuWindow(
            String publisherInstanceId, String sampleSeq, String startUptimeMs, String endUptimeMs,
            int partIndex, int partCount, String threadsStatus, Integer rowsSeen, int rowsReturned) {
    }
}
