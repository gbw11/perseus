package com.perseus.backend.metrics.application;

import com.perseus.backend.agent.application.AgentDataService;
import com.perseus.backend.agent.domain.MetricSample;
import com.perseus.backend.agent.domain.MetricValue;
import com.perseus.backend.agent.domain.ProcessInfo;
import com.perseus.backend.agent.domain.ProcessSnapshot;
import com.perseus.backend.common.error.ApiException;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Comparator;
import java.util.List;
import java.util.function.Function;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

@Service
public class MetricsQueryService {

    static final Duration DEFAULT_RANGE = Duration.ofMinutes(15);
    static final Duration MAX_RANGE = Duration.ofHours(24);

    private final AgentDataService agentDataService;
    private final Clock clock;

    public MetricsQueryService(AgentDataService agentDataService, Clock clock) {
        this.agentDataService = agentDataService;
        this.clock = clock;
    }

    public List<FilteredSample> history(String vmId, Instant from, Instant to, MetricFilter filter) {
        Instant end = to == null ? clock.instant() : to;
        Instant start = from == null ? end.minus(DEFAULT_RANGE) : from;
        if (!start.isBefore(end)) {
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "TIME_RANGE_INVALID", "from은 to보다 앞서야 합니다.");
        }
        if (Duration.between(start, end).compareTo(MAX_RANGE) > 0) {
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "TIME_RANGE_INVALID", "조회 기간은 최대 24시간입니다.");
        }
        return agentDataService.findMetricSamples(vmId, start, end).stream()
                .filter(filter::acceptsSource)
                .map(sample -> new FilteredSample(sample, filter.select(sample)))
                .filter(filtered -> !filtered.values().isEmpty())
                .toList();
    }

    public Processes processes(String vmId, ProcessSort sort, int limit) {
        if (limit < 1 || limit > 20) {
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "limit은 1~20이어야 합니다.");
        }
        ProcessSnapshot snapshot = agentDataService.findProcessSnapshot(vmId).orElse(null);
        if (snapshot == null) {
            return new Processes(vmId, null, null, true, List.of());
        }
        List<ProcessInfo> items = snapshot.getProcesses().stream()
                .sorted(Comparator.comparing(sort.key, Comparator.nullsLast(Comparator.reverseOrder())))
                .limit(limit)
                .toList();
        return new Processes(
                vmId,
                snapshot.getBootId(),
                snapshot.getObservedAt(),
                agentDataService.isStale(snapshot.getObservedAt(), clock.instant()),
                items);
    }

    public record FilteredSample(MetricSample sample, List<MetricValue> values) {
    }

    public record Processes(String vmId, String bootId, Instant observedAt, boolean stale, List<ProcessInfo> items) {
    }

    public enum ProcessSort {
        CPU_PERCENT("cpu_percent", process -> process.cpuPercent()),
        RSS_BYTES("rss_bytes", process -> process.rssBytes() == null ? null : process.rssBytes().doubleValue());

        private final String value;
        private final Function<ProcessInfo, Double> key;

        ProcessSort(String value, Function<ProcessInfo, Double> key) {
            this.value = value;
            this.key = key;
        }

        public static ProcessSort fromValue(String value) {
            for (ProcessSort sort : values()) {
                if (sort.value.equals(value)) {
                    return sort;
                }
            }
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "지원하지 않는 sort입니다: " + value);
        }
    }
}
