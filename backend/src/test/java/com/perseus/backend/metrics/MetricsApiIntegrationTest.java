package com.perseus.backend.metrics;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.perseus.backend.agent.application.AgentDataService;
import com.perseus.backend.agent.domain.MetricSample;
import com.perseus.backend.agent.domain.MetricSampleRepository;
import com.perseus.backend.agent.domain.MetricValue;
import com.perseus.backend.agent.domain.ProcessInfo;
import com.perseus.backend.agent.domain.ProcessSnapshotRepository;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Map;
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
class MetricsApiIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"metrics-user","password":"password123!"}
            """;
    private static final String VM = "vm-metrics";

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private AgentDataService agentDataService;

    @Autowired
    private MetricSampleRepository metricSampleRepository;

    @Autowired
    private ProcessSnapshotRepository processSnapshotRepository;

    private String accessToken;

    @BeforeEach
    void setUp() throws Exception {
        metricSampleRepository.deleteAll();
        processSnapshotRepository.deleteAll();
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
    void historyDefaultsToTheLastFifteenMinutesInTimeOrder() throws Exception {
        Instant now = Instant.now().truncatedTo(ChronoUnit.SECONDS);
        record("s-old", now.minusSeconds(20 * 60), 5);
        record("s-2", now.minusSeconds(10), 30);
        record("s-1", now.minusSeconds(20), 20);

        perform("/metrics")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(2))
                .andExpect(jsonPath("$.items[0].sample_id").value("s-1"))
                .andExpect(jsonPath("$.items[0].source").value("guest_agent"))
                .andExpect(jsonPath("$.items[0].window_ms").value(2000))
                .andExpect(jsonPath("$.items[1].values[0].name").value("cpu_percent_allocated"))
                .andExpect(jsonPath("$.items[1].values[0].value").value(30.0))
                .andExpect(jsonPath("$.items[1].values[1].unavailable_reason").value("not supported"));
    }

    @Test
    void historyFiltersByMetricNameAndTimeRange() throws Exception {
        Instant base = Instant.parse("2026-10-16T03:00:00Z");
        record("s-1", base, 20);
        record("s-2", base.plusSeconds(60), 40);

        perform("/metrics?from=" + base.plusSeconds(30) + "&to=" + base.plusSeconds(120) + "&names=memory_used_bytes")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(1))
                .andExpect(jsonPath("$.items[0].sample_id").value("s-2"))
                .andExpect(jsonPath("$.items[0].values.length()").value(1))
                .andExpect(jsonPath("$.items[0].values[0].name").value("memory_used_bytes"));

        perform("/metrics?from=" + base.plusSeconds(30) + "&to=" + base.plusSeconds(120) + "&source=xen")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isEmpty());
    }

    @Test
    void historyRejectsInvalidRangeAndSource() throws Exception {
        Instant base = Instant.parse("2026-10-16T03:00:00Z");
        perform("/metrics?from=" + base + "&to=" + base)
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("TIME_RANGE_INVALID"));
        perform("/metrics?from=" + base + "&to=" + base.plusSeconds(25 * 3600))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("TIME_RANGE_INVALID"));
        perform("/metrics?source=dom0")
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
    }

    @Test
    void processesAreSortedAndLimitedFromTheLatestSnapshot() throws Exception {
        agentDataService.recordMetrics(sample("s-1", Instant.now(), 10), List.of(
                new ProcessInfo(1, "init", 0.1, 4_000_000L),
                new ProcessInfo(42, "stress", 95.5, 1_000_000L),
                new ProcessInfo(7, "agent", 2.0, 30_000_000L)));

        perform("/processes?limit=2")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.vm_id").value(VM))
                .andExpect(jsonPath("$.boot_id").value("boot-1"))
                .andExpect(jsonPath("$.stale").value(false))
                .andExpect(jsonPath("$.items.length()").value(2))
                .andExpect(jsonPath("$.items[0].name").value("stress"))
                .andExpect(jsonPath("$.items[1].name").value("agent"));

        perform("/processes?sort=rss_bytes")
                .andExpect(jsonPath("$.items[0].name").value("agent"));
    }

    @Test
    void processesWithoutAnyReportAreEmptyAndStale() throws Exception {
        perform("/processes")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.observed_at").isEmpty())
                .andExpect(jsonPath("$.stale").value(true))
                .andExpect(jsonPath("$.items").isEmpty());

        perform("/processes?limit=21").andExpect(status().isUnprocessableEntity());
        perform("/processes?sort=pid").andExpect(status().isUnprocessableEntity());
    }

    private ResultActions perform(String path) throws Exception {
        return mockMvc.perform(get("/api/v1/vms/" + VM + path)
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken));
    }

    private void record(String sampleId, Instant observedAt, double cpu) {
        agentDataService.recordMetrics(sample(sampleId, observedAt, cpu), List.of());
    }

    private MetricSample sample(String sampleId, Instant observedAt, double cpu) {
        return new MetricSample(sampleId, VM, "boot-1", "guest_agent", observedAt, observedAt, 2000, List.of(
                new MetricValue("cpu_percent_allocated", cpu, "percent", Map.of(), null),
                new MetricValue("net_rx_bytes_total", null, "bytes", Map.of("iface", "eth0"), "not supported"),
                new MetricValue("memory_used_bytes", 1.0e8, "bytes", Map.of(), null)));
    }
}
