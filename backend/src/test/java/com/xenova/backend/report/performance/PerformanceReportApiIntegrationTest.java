package com.xenova.backend.report.performance;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.report.performance.application.PerformanceReportService;
import com.xenova.backend.report.performance.domain.MeasurementEnvironment;
import com.xenova.backend.report.performance.domain.NewPerformanceTrial;
import com.xenova.backend.report.performance.domain.PerformanceMetric;
import com.xenova.backend.report.performance.domain.PerformanceTrialRepository;
import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.ResultActions;

@SpringBootTest
@AutoConfigureMockMvc
class PerformanceReportApiIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"report-perf-user","nickname":"성능 리포트 테스트","password":"password123!"}
            """;
    private static final Instant BASE = Instant.parse("2026-10-16T03:00:00Z");

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private PerformanceReportService service;

    @Autowired
    private PerformanceTrialRepository repository;

    private String accessToken;

    @BeforeEach
    void setUp() throws Exception {
        repository.deleteAll();
        mockMvc.perform(post("/api/v1/auth/signup")
                .contentType(MediaType.APPLICATION_JSON)
                .content(CREDENTIALS));
        String body = mockMvc.perform(post("/api/v1/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(CREDENTIALS))
                .andReturn().getResponse().getContentAsString();
        accessToken = objectMapper.readTree(body).get("access_token").asText();
    }

    @Test
    void reportListsTrialsAndSummarizesOnlySuccessfulOnes() throws Exception {
        recovery("vm-1", 0, 8000L, true);
        recovery("vm-1", 1, 9000L, true);
        recovery("vm-1", 2, 10000L, true);
        recovery("vm-1", 3, null, false);
        boot("vm-2", 4, 12000L);

        report("?metric=vm_recovery_ms")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(4))
                .andExpect(jsonPath("$.items[0].metric").value("vm_recovery_ms"))
                .andExpect(jsonPath("$.items[0].duration_ms").value(8000))
                .andExpect(jsonPath("$.items[3].success").value(false))
                .andExpect(jsonPath("$.items[3].failure_reason").value("복구 시간 초과"))
                .andExpect(jsonPath("$.summary.length()").value(1))
                .andExpect(jsonPath("$.summary[0].environment").value("rpi4"))
                .andExpect(jsonPath("$.summary[0].count").value(4))
                .andExpect(jsonPath("$.summary[0].success_count").value(3))
                .andExpect(jsonPath("$.summary[0].failure_count").value(1))
                .andExpect(jsonPath("$.summary[0].median_ms").value(9000))
                .andExpect(jsonPath("$.summary[0].p95_ms").value(10000));
    }

    @Test
    void reportFiltersByVmAndTimeRange() throws Exception {
        recovery("vm-1", 0, 8000L, true);
        boot("vm-2", 10, 12000L);
        boot("vm-2", 20, 13000L);

        report("?vm_id=vm-2&from=" + BASE.plusSeconds(15) + "&to=" + BASE.plusSeconds(60))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].duration_ms").value(13000));
    }

    @Test
    void reportWithoutTrialsReturnsEmptyLists() throws Exception {
        report("")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isEmpty())
                .andExpect(jsonPath("$.summary").isEmpty());
    }

    @Test
    void reportRejectsInvalidParameters() throws Exception {
        report("?metric=cpu_ms")
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
        report("?from=" + BASE.plusSeconds(60) + "&to=" + BASE)
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("TIME_RANGE_INVALID"));
        report("?from=yesterday")
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.errors[0].field").value("from"));
    }

    private ResultActions report(String query) throws Exception {
        return mockMvc.perform(get("/api/v1/reports/performance" + query)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken));
    }

    private void recovery(String vmId, int offsetSeconds, Long durationMs, boolean success) {
        record(PerformanceMetric.VM_RECOVERY_MS, vmId, offsetSeconds, durationMs, success,
                success ? null : "복구 시간 초과");
    }

    private void boot(String vmId, int offsetSeconds, Long durationMs) {
        record(PerformanceMetric.VM_BOOT_MS, vmId, offsetSeconds, durationMs, true, null);
    }

    private void record(PerformanceMetric metric, String vmId, int offsetSeconds, Long durationMs,
            boolean success, String failureReason) {
        Instant startedAt = BASE.plusSeconds(offsetSeconds);
        service.record(new NewPerformanceTrial(
                metric, "v1", vmId, null, null, "build-1", "12", null,
                startedAt, durationMs == null ? null : startedAt.plusMillis(durationMs), durationMs,
                success, failureReason, MeasurementEnvironment.RPI4, null));
    }
}
