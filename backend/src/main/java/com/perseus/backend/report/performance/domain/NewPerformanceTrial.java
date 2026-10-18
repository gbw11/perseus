package com.perseus.backend.report.performance.domain;

import java.time.Instant;

// A finished measurement reported by the recovery engine, VM lifecycle or message probe.
public record NewPerformanceTrial(
        PerformanceMetric metric,
        String definitionId,
        String vmId,
        String sourceVmId,
        String targetVmId,
        String buildId,
        String templateVersion,
        String operationId,
        Instant startedAt,
        Instant finishedAt,
        Long durationMs,
        boolean success,
        String failureReason,
        MeasurementEnvironment environment,
        String rawLogRef) {
}
