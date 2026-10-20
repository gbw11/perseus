package com.xenova.backend.metrics.api;

import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricValue;
import java.time.Instant;
import java.util.List;

// Same shape for REST-12 history and WS-01 live samples, so the client can merge them.
public record MetricSampleResponse(
        String sampleId,
        String vmId,
        String bootId,
        String source,
        Instant observedAt,
        Instant receivedAt,
        Integer windowMs,
        List<MetricValue> values) {

    public static MetricSampleResponse of(MetricSample sample, List<MetricValue> values) {
        return new MetricSampleResponse(
                sample.getSampleId(),
                sample.getVmId(),
                sample.getBootId(),
                sample.getSource(),
                sample.getObservedAt(),
                sample.getReceivedAt(),
                sample.getWindowMs(),
                values);
    }
}
