package com.xenova.backend.agent.domain;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.List;

/** Sample-level wire context stored alongside the values in the existing JSON column. */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record MetricSampleData(List<MetricValue> values, NetworkObservation network, CpuWindow cpuWindow,
        TelemetryObservation telemetry) {

    public MetricSampleData(List<MetricValue> values, NetworkObservation network, CpuWindow cpuWindow) {
        this(values, network, cpuWindow, null);
    }

    public MetricSampleData {
        values = values == null ? List.of() : List.copyOf(values);
    }

    public record NetworkObservation(String publisherInstanceId, String observedUptimeMs) {
    }

    public record CpuWindow(
            String publisherInstanceId, String sampleSeq, String startUptimeMs, String endUptimeMs,
            int partIndex, int partCount, String threadsStatus, Integer rowsSeen, int rowsReturned) {
    }

    /** Source cache time is distinct from collection, send and server receipt times. */
    public record TelemetryObservation(
            String family, String publisherInstanceId, String sourceSampleSeq, String observedUptimeMs,
            String sentUptimeMs, String sentAt, String utcQuality, String utcSource, Integer uncertaintyMs,
            CollectionObservation collection, ExecutionContext context, BufferObservation buffer, String messageSeq) {

        public TelemetryObservation(String family, String publisherInstanceId, String sourceSampleSeq,
                String observedUptimeMs, String sentUptimeMs, String sentAt, String utcQuality, String utcSource,
                Integer uncertaintyMs, CollectionObservation collection, ExecutionContext context) {
            this(family, publisherInstanceId, sourceSampleSeq, observedUptimeMs, sentUptimeMs, sentAt,
                    utcQuality, utcSource, uncertaintyMs, collection, context, null, null);
        }
    }

    /** MANUAL owner state is separate from metric collection success and grants no output permission. */
    public record BufferObservation(
            String phase, String contentBytes, String sequence, String sourceUptimeMs, String ownerLifetime,
            String contextRevision, String connectionRevision, String inputStatus, int sourceRc,
            Boolean performed, boolean revoked, String lastReadSequence) {
    }

    public record CollectionObservation(
            String sampleId, String startUptimeMs, String endUptimeMs, String elapsedUs,
            String status, Integer sourceRc) {
    }

    public record ExecutionContext(String binding, String hostBootId, String executionId, String receiptId) {
    }
}
