package com.perseus.backend.report.performance.api;

import com.perseus.backend.common.error.ApiException;
import com.perseus.backend.report.performance.application.PerformanceReportService;
import com.perseus.backend.report.performance.application.PerformanceReportService.PerformanceReport;
import com.perseus.backend.report.performance.application.PerformanceReportService.PerformanceSummary;
import com.perseus.backend.report.performance.domain.MeasurementEnvironment;
import com.perseus.backend.report.performance.domain.PerformanceMetric;
import com.perseus.backend.report.performance.domain.PerformanceTrial;
import java.time.Instant;
import java.util.List;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/reports/performance")
public class PerformanceReportController {

    private final PerformanceReportService service;

    public PerformanceReportController(PerformanceReportService service) {
        this.service = service;
    }

    @GetMapping
    public PerformanceReportResponse report(
            @RequestParam(required = false) String metric,
            @RequestParam(name = "vm_id", required = false) String vmId,
            @RequestParam(required = false) Instant from,
            @RequestParam(required = false) Instant to) {
        PerformanceMetric parsedMetric = metric == null ? null : PerformanceMetric.fromValue(metric)
                .orElseThrow(() -> new ApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", "지원하지 않는 metric입니다: " + metric));

        PerformanceReport report = service.report(parsedMetric, vmId, from, to);
        return new PerformanceReportResponse(
                report.trials().stream().map(TrialResponse::from).toList(),
                report.summary().stream().map(SummaryResponse::from).toList());
    }

    public record PerformanceReportResponse(List<TrialResponse> items, List<SummaryResponse> summary) {
    }

    public record TrialResponse(
            String trialId,
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

        static TrialResponse from(PerformanceTrial trial) {
            return new TrialResponse(
                    trial.getTrialId(),
                    trial.getMetric(),
                    trial.getDefinitionId(),
                    trial.getVmId(),
                    trial.getSourceVmId(),
                    trial.getTargetVmId(),
                    trial.getBuildId(),
                    trial.getTemplateVersion(),
                    trial.getOperationId(),
                    trial.getStartedAt(),
                    trial.getFinishedAt(),
                    trial.getDurationMs(),
                    trial.isSuccess(),
                    trial.getFailureReason(),
                    trial.getEnvironment(),
                    trial.getRawLogRef());
        }
    }

    public record SummaryResponse(
            PerformanceMetric metric,
            String definitionId,
            MeasurementEnvironment environment,
            long count,
            long successCount,
            long failureCount,
            Long medianMs,
            Long p95Ms) {

        static SummaryResponse from(PerformanceSummary summary) {
            return new SummaryResponse(
                    summary.metric(),
                    summary.definitionId(),
                    summary.environment(),
                    summary.count(),
                    summary.successCount(),
                    summary.failureCount(),
                    summary.medianMs(),
                    summary.p95Ms());
        }
    }
}
