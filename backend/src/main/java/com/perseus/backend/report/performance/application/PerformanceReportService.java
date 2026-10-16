package com.perseus.backend.report.performance.application;

import com.perseus.backend.common.error.ApiException;
import com.perseus.backend.report.performance.domain.MeasurementEnvironment;
import com.perseus.backend.report.performance.domain.NewPerformanceTrial;
import com.perseus.backend.report.performance.domain.PerformanceMetric;
import com.perseus.backend.report.performance.domain.PerformanceTrial;
import com.perseus.backend.report.performance.domain.PerformanceTrialRepository;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import org.springframework.data.domain.Sort;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class PerformanceReportService {

    private final PerformanceTrialRepository repository;

    public PerformanceReportService(PerformanceTrialRepository repository) {
        this.repository = repository;
    }

    @Transactional
    public PerformanceTrial record(NewPerformanceTrial trial) {
        return repository.save(new PerformanceTrial(UUID.randomUUID().toString(), trial));
    }

    @Transactional(readOnly = true)
    public PerformanceReport report(PerformanceMetric metric, String vmId, Instant from, Instant to) {
        if (from != null && to != null && !from.isBefore(to)) {
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "TIME_RANGE_INVALID", "from은 to보다 앞서야 합니다.");
        }

        Specification<PerformanceTrial> filter = Specification.allOf(
                metric == null ? null : (root, query, cb) -> cb.equal(root.get("metric"), metric),
                vmId == null ? null : (root, query, cb) -> cb.equal(root.get("vmId"), vmId),
                from == null ? null : (root, query, cb) -> cb.greaterThanOrEqualTo(root.get("startedAt"), from),
                to == null ? null : (root, query, cb) -> cb.lessThan(root.get("startedAt"), to));
        List<PerformanceTrial> trials = repository.findAll(filter, Sort.by("startedAt"));

        return new PerformanceReport(trials, summarize(trials));
    }

    // Only successful trials feed the statistics; failures are still counted so they stay visible.
    private List<PerformanceSummary> summarize(List<PerformanceTrial> trials) {
        Map<SummaryKey, List<PerformanceTrial>> groups = new LinkedHashMap<>();
        trials.stream()
                .sorted(Comparator.comparing((PerformanceTrial trial) -> trial.getMetric())
                        .thenComparing(PerformanceTrial::getDefinitionId)
                        .thenComparing(PerformanceTrial::getEnvironment))
                .forEach(trial -> groups.computeIfAbsent(SummaryKey.of(trial), key -> new ArrayList<>()).add(trial));

        return groups.entrySet().stream().map(entry -> {
            List<PerformanceTrial> group = entry.getValue();
            List<Long> durations = group.stream()
                    .filter(PerformanceTrial::isSuccess)
                    .map(PerformanceTrial::getDurationMs)
                    .filter(Objects::nonNull)
                    .sorted()
                    .toList();
            long successCount = group.stream().filter(PerformanceTrial::isSuccess).count();
            SummaryKey key = entry.getKey();
            return new PerformanceSummary(
                    key.metric(),
                    key.definitionId(),
                    key.environment(),
                    group.size(),
                    successCount,
                    group.size() - successCount,
                    DurationStatistics.median(durations),
                    DurationStatistics.p95(durations));
        }).toList();
    }

    public record PerformanceReport(List<PerformanceTrial> trials, List<PerformanceSummary> summary) {
    }

    public record PerformanceSummary(
            PerformanceMetric metric,
            String definitionId,
            MeasurementEnvironment environment,
            long count,
            long successCount,
            long failureCount,
            Long medianMs,
            Long p95Ms) {
    }

    private record SummaryKey(PerformanceMetric metric, String definitionId, MeasurementEnvironment environment) {

        static SummaryKey of(PerformanceTrial trial) {
            return new SummaryKey(trial.getMetric(), trial.getDefinitionId(), trial.getEnvironment());
        }
    }
}
