package com.xenova.backend.metrics.api;

import com.xenova.backend.metrics.application.MetricFilter;
import com.xenova.backend.metrics.application.MetricsQueryService;
import com.xenova.backend.metrics.application.MetricsQueryService.Processes;
import com.xenova.backend.metrics.application.MetricsQueryService.ProcessSort;
import java.time.Instant;
import java.util.List;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/vms/{vmId}")
public class MetricsController {

    private final MetricsQueryService service;

    public MetricsController(MetricsQueryService service) {
        this.service = service;
    }

    // REST-12: history for the initial chart and for filling gaps after a WebSocket reconnect.
    @GetMapping("/metrics")
    public MetricHistoryResponse metrics(
            @PathVariable String vmId,
            @RequestParam(required = false) Instant from,
            @RequestParam(required = false) Instant to,
            @RequestParam(required = false) String source,
            @RequestParam(required = false) String names) {
        return new MetricHistoryResponse(service.history(vmId, from, to, MetricFilter.fromQuery(source, names))
                .stream()
                .map(filtered -> MetricSampleResponse.of(filtered.sample(), filtered.values()))
                .toList());
    }

    // REST-13: latest top processes reported with the metrics.
    @GetMapping("/processes")
    public Processes processes(
            @PathVariable String vmId,
            @RequestParam(defaultValue = "cpu_percent") String sort,
            @RequestParam(defaultValue = "10") int limit) {
        return service.processes(vmId, ProcessSort.fromValue(sort), limit);
    }

    public record MetricHistoryResponse(List<MetricSampleResponse> items) {
    }
}
