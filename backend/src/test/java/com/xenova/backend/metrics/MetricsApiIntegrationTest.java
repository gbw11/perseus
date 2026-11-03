package com.xenova.backend.metrics;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.xenova.backend.agent.application.AgentDataService;
import com.xenova.backend.agent.application.AgentMessageHandler;
import com.xenova.backend.agent.application.AgentMessageHandler.Result;
import com.xenova.backend.agent.application.TelemetryHeartbeatMessageHandler;
import com.xenova.backend.agent.application.VmTelemetryMessageHandler;
import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricSampleRepository;
import com.xenova.backend.agent.domain.MetricValue;
import com.xenova.backend.agent.domain.ProcessInfo;
import com.xenova.backend.agent.domain.ProcessSnapshotRepository;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Map;
import java.util.UUID;
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
            {"username":"metrics-user","nickname":"메트릭 테스트","password":"password123!"}
            """;
    private static final String VM = "vm-metrics";
    private static final String ZEPHYR_VM = "f1000000-0000-4000-8000-000000000001";

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

    @Autowired
    private VmRecordRepository vmRecords;

    @Autowired
    private AgentMessageHandler agentHandler;

    @Autowired
    private VmTelemetryMessageHandler zephyrHandler;

    @Autowired
    private TelemetryHeartbeatMessageHandler heartbeatHandler;

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
                .andExpect(jsonPath("$.process_collection").hasJsonPath())
                .andExpect(jsonPath("$.process_collection").doesNotExist())
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

    @Test
    void linuxProcessCollectionStaysWithTheLatestProcessRows() throws Exception {
        Instant start = Instant.now().truncatedTo(ChronoUnit.SECONDS).minusSeconds(6);
        String partial = """
                {"status":"partial","discovered":5,"read_ok":3,"vanished":1,"failed":1,"truncated":true,"error":null}
                """;
        String readFailed = """
                {"status":"read_failed","discovered":0,"read_ok":0,"vanished":0,"failed":0,"truncated":false,
                 "error":"PermissionError(EACCES)"}
                """;
        String rows = """
                [{"pid":2,"name":"worker","cpu_percent":4.5,"rss_bytes":4096}]
                """;
        assertThat(publishProcesses("proc-1", start, rows, partial)).isEqualTo(Result.ACCEPTED);
        assertThat(publishProcesses("proc-1", start.plusSeconds(1), "[]", readFailed)).isEqualTo(Result.IGNORED);
        assertThat(publishProcesses("proc-old", start.minusSeconds(1), "[]", readFailed)).isEqualTo(Result.ACCEPTED);
        perform("/processes?limit=1")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].name").value("worker"))
                .andExpect(jsonPath("$.process_collection.status").value("partial"))
                .andExpect(jsonPath("$.process_collection.discovered").value(5))
                .andExpect(jsonPath("$.process_collection.read_ok").value(3))
                .andExpect(jsonPath("$.process_collection.vanished").value(1))
                .andExpect(jsonPath("$.process_collection.failed").value(1))
                .andExpect(jsonPath("$.process_collection.truncated").value(true));

        assertThat(publishProcesses("proc-2", start.plusSeconds(2), "[]", readFailed)).isEqualTo(Result.ACCEPTED);
        perform("/processes")
                .andExpect(jsonPath("$.items").isEmpty())
                .andExpect(jsonPath("$.process_collection.status").value("read_failed"))
                .andExpect(jsonPath("$.process_collection.error").value("PermissionError(EACCES)"));

        String malformed = readFailed.replace("PermissionError(EACCES)", "/proc/private/path");
        assertThat(publishProcesses("proc-3", start.plusSeconds(3), rows, malformed)).isEqualTo(Result.ACCEPTED);
        perform("/processes")
                .andExpect(jsonPath("$.items[0].name").value("worker"))
                .andExpect(jsonPath("$.process_collection").doesNotExist());
    }

    private Result publishProcesses(String id, Instant observedAt, String rows, String collection) {
        String json = """
                {"schema_version":"1.0","type":"agent.metrics","vm_id":"%s","message_id":"%s",
                 "boot_id":"boot-1","agent_instance_id":"agent-1","seq":1,"sent_at":"%s",
                 "payload":{"window_ms":2000,"values":[{"name":"cpu_percent_allocated","unit":"percent","value":4.5}],
                 "processes":%s,"process_collection":%s}}
                """.formatted(VM, id, observedAt, rows, collection);
        return agentHandler.handle("xenova/v1/vms/" + VM + "/metrics", json.getBytes(StandardCharsets.UTF_8),
                Instant.now());
    }

    @Test
    void zephyrNetworkHistoryKeepsUint64AndSeparatesAResetAndAnUnavailableSample() throws Exception {
        vmRecords.save(VmRecord.registeredByPlatform(ZEPHYR_VM, "zephyr-metrics", "zephyr-fixture", "1", 1, 16,
                GuestKind.ZEPHYR, Instant.now(), null));
        Instant start = Instant.now().truncatedTo(ChronoUnit.SECONDS).minusSeconds(5);
        String old = network("18446744073709551615", "9007199254740993", "1", "9000", null, 0, 1);
        assertThat(publishNetwork(old, start)).isEqualTo(Result.ACCEPTED);
        assertThat(publishNetwork(old, start.plusMillis(100))).isEqualTo(Result.IGNORED);
        assertThat(publishNetwork(network("0", "1", "2", "10000", null, 0, 2), start.plusSeconds(1)))
                .isEqualTo(Result.ACCEPTED);
        assertThat(publishNetwork(network(null, null, "2", null, "stale_sample", -110, 3), start.plusSeconds(2)))
                .isEqualTo(Result.ACCEPTED);
        assertThat(publishNetwork(network("18446744073709551616", "1", "2", "11000", null, 0, 4),
                start.plusSeconds(3))).isEqualTo(Result.REJECTED);
        assertThat(processSnapshotRepository.findById(ZEPHYR_VM)).isEmpty();

        perform(ZEPHYR_VM, "/metrics?source=zephyr_telemetry")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items.length()").value(3))
                .andExpect(jsonPath("$.items[0].network.observed_uptime_ms").value("9000"))
                .andExpect(jsonPath("$.items[0].values[0].counter_value").value("18446744073709551615"))
                .andExpect(jsonPath("$.items[0].values[1].counter_value").value("9007199254740993"))
                .andExpect(jsonPath("$.items[0].values[0].labels.counter_epoch").value("1"))
                .andExpect(jsonPath("$.items[1].values[0].labels.counter_epoch").value("2"))
                .andExpect(jsonPath("$.items[1].values[0].counter_value").value("0"))
                .andExpect(jsonPath("$.items[2].network.observed_uptime_ms").isEmpty())
                .andExpect(jsonPath("$.items[2].values[0].counter_value").doesNotExist())
                .andExpect(jsonPath("$.items[2].values[0].source_rc").value(-110))
                .andExpect(jsonPath("$.items[2].values[0].unavailable_reason").value("stale_sample"));
    }

    @Test
    void threadsAssembleOneWindowInAnyPageOrderAndExposeIncompleteOrConflictingEvidence() throws Exception {
        String publisher = "f3000000-0000-4000-8000-000000000001";
        Instant start = Instant.now().truncatedTo(ChronoUnit.SECONDS).minusSeconds(8);
        ObjectNode lastPage = cpuPage(publisher, "11", 1, 5, "last");
        assertThat(publishCpu(lastPage, start)).isEqualTo(Result.ACCEPTED);
        assertThat(publishCpu(lastPage, start.plusMillis(100))).isEqualTo(Result.IGNORED);
        perform(ZEPHYR_VM, "/threads")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.complete").value(false))
                .andExpect(jsonPath("$.parts_received").value(1))
                .andExpect(jsonPath("$.items[0].thread_name").value("last"));

        assertThat(publishCpu(cpuPage(publisher, "11", 0, 5, "same-name"), start.plusSeconds(1)))
                .isEqualTo(Result.ACCEPTED);
        assertThat(publishCpu(cpuPage(publisher, "10", 0, 4, "delayed"), start.plusSeconds(2)))
                .isEqualTo(Result.ACCEPTED);
        perform(ZEPHYR_VM, "/threads")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.complete").value(true))
                .andExpect(jsonPath("$.sample_seq").value("11"))
                .andExpect(jsonPath("$.window_ms").value(1000))
                .andExpect(jsonPath("$.items.length()").value(5))
                .andExpect(jsonPath("$.items[0].thread_id").value("1"))
                .andExpect(jsonPath("$.items[0].thread_name").value("same-name"))
                .andExpect(jsonPath("$.items[1].thread_id").value("2"))
                .andExpect(jsonPath("$.items[1].thread_name").value("same-name"));

        assertThat(publishCpu(cpuPage(publisher, "11", 0, 5, "conflict"), start.plusSeconds(3)))
                .isEqualTo(Result.ACCEPTED);
        perform(ZEPHYR_VM, "/threads")
                .andExpect(jsonPath("$.complete").value(false))
                .andExpect(jsonPath("$.conflicting_pages").value(true))
                .andExpect(jsonPath("$.items").isEmpty());

        String replacement = "f3000000-0000-4000-8000-000000000002";
        assertThat(publishCpu(cpuPage(replacement, "1", 0, 1, "replacement"), start.plusSeconds(4)))
                .isEqualTo(Result.ACCEPTED);
        assertThat(publishCpu(cpuPage(publisher, "12", 0, 1, "old-run"), start.plusSeconds(5)))
                .isEqualTo(Result.ACCEPTED);
        perform(ZEPHYR_VM, "/threads")
                .andExpect(jsonPath("$.complete").value(true))
                .andExpect(jsonPath("$.publisher_instance_id").value(replacement))
                .andExpect(jsonPath("$.items[0].thread_name").value("replacement"));
        ObjectNode truncated = cpuPage(replacement, "2", 0, 1, "긴 이름");
        ((ObjectNode) truncated.at("/payload/window")).put("threads_status", "truncated").put("rows_seen", 2);
        ((ObjectNode) truncated.at("/payload/samples/1/labels")).put("name_truncated", "true");
        assertThat(publishCpu(truncated, start.plusSeconds(6))).isEqualTo(Result.ACCEPTED);
        perform(ZEPHYR_VM, "/threads")
                .andExpect(jsonPath("$.complete").value(true))
                .andExpect(jsonPath("$.threads_status").value("truncated"))
                .andExpect(jsonPath("$.rows_seen").value(2))
                .andExpect(jsonPath("$.items[0].thread_name").value("긴 이름"))
                .andExpect(jsonPath("$.items[0].name_truncated").value(true));
        perform("/threads").andExpect(jsonPath("$.items").isEmpty()).andExpect(jsonPath("$.stale").value(true));
    }

    @Test
    void publisherHeartbeatExpiresWithoutClearingTheLastReportAndRecoversWithTheSameFailureTotal() throws Exception {
        String vm = "f1000000-0000-4000-8000-000000000003";
        String boot = "f2000000-0000-4000-8000-000000000001";
        String publisher = "f3000000-0000-4000-8000-000000000001";
        String replacement = "f3000000-0000-4000-8000-000000000002";
        String statusPath = "/telemetry/heartbeat";
        BigInteger failures = new BigInteger("18446744073709551615");
        Instant now = Instant.now().truncatedTo(ChronoUnit.MILLIS);
        Instant expired = now.minusSeconds(7);
        perform(vm, statusPath)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UNKNOWN"))
                .andExpect(jsonPath("$.publisher_state").doesNotExist())
                .andExpect(jsonPath("$.received_at").isEmpty());

        ObjectNode degraded = heartbeat(vm, boot, publisher, "9007199254740993", "50000", "degraded", "49900");
        assertThat(publishHeartbeat(degraded, expired)).isEqualTo(Result.ACCEPTED);
        assertThat(publishHeartbeat(degraded, now)).isEqualTo(Result.IGNORED);
        assertThat(publishHeartbeat(heartbeat(vm, boot, publisher, "9007199254740992", "50000", "running", "49900"), now))
                .isEqualTo(Result.IGNORED);
        perform(vm, statusPath)
                .andExpect(jsonPath("$.status").value("STALE"))
                .andExpect(jsonPath("$.publisher_state").value("degraded"))
                .andExpect(jsonPath("$.received_at").value(expired.toString()))
                .andExpect(jsonPath("$.last_collection_success_uptime_ms").value("49900"))
                .andExpect(result -> assertThat(objectMapper.readTree(result.getResponse().getContentAsByteArray())
                        .path("collection_failures_total").bigIntegerValue()).isEqualTo(failures));

        assertThat(publishHeartbeat(heartbeat(vm, boot, publisher, "9007199254740994", "52000", "running", "51900"), now))
                .isEqualTo(Result.ACCEPTED);
        perform(vm, statusPath)
                .andExpect(jsonPath("$.status").value("FRESH"))
                .andExpect(jsonPath("$.publisher_state").value("running"))
                .andExpect(jsonPath("$.last_collection_success_age_ms").value(100))
                .andExpect(jsonPath("$.last_collection_success_uptime_ms").value("51900"))
                .andExpect(result -> assertThat(objectMapper.readTree(result.getResponse().getContentAsByteArray())
                        .path("collection_failures_total").bigIntegerValue()).isEqualTo(failures));

        assertThat(publishHeartbeat(heartbeat(vm, boot, replacement, "1", "52100", "stopping", null), now))
                .isEqualTo(Result.ACCEPTED);
        assertThat(publishHeartbeat(heartbeat(vm, boot, publisher, "9007199254740995", "54000", "degraded", null), now))
                .isEqualTo(Result.IGNORED);
        assertThat(publishHeartbeat(heartbeat(vm, boot, replacement, "2", "52000", "sleeping", "52001"), now))
                .isEqualTo(Result.REJECTED);
        perform(vm, statusPath)
                .andExpect(jsonPath("$.publisher_state").value("stopping"))
                .andExpect(jsonPath("$.last_collection_success_uptime_ms").isEmpty())
                .andExpect(jsonPath("$.publisher_instance_id").value(replacement));

        String reboot = "f2000000-0000-4000-8000-000000000002";
        assertThat(publishHeartbeat(heartbeat(vm, reboot, publisher, "1", "10", "running", null), now))
                .isEqualTo(Result.ACCEPTED);
        assertThat(publishHeartbeat(heartbeat(vm, boot, replacement, "3", "55000", "stopping", null), now))
                .isEqualTo(Result.IGNORED);
        perform(vm, statusPath)
                .andExpect(jsonPath("$.boot_id").value(reboot))
                .andExpect(jsonPath("$.publisher_state").value("running"));
        perform(ZEPHYR_VM, statusPath).andExpect(jsonPath("$.status").value("UNKNOWN"));
    }

    @Test
    void publisherMetricSourceFollowsRegisteredGuestKind() throws Exception {
        String linux = "f1000000-0000-4000-8000-000000000004";
        String zephyr = "f1000000-0000-4000-8000-000000000005";
        vmRecords.save(VmRecord.registeredByPlatform(linux, "publisher-linux", "linux-fixture", "1", 1, 128,
                GuestKind.LINUX, Instant.now(), null));
        vmRecords.save(VmRecord.registeredByPlatform(zephyr, "publisher-zephyr", "zephyr-fixture", "1", 1, 16,
                GuestKind.ZEPHYR, Instant.now(), null));
        for (String vm : List.of(linux, zephyr)) {
            String payload = """
                    {"schema_version":"2.1","type":"telemetry.metrics","vm_id":"%s","message_id":"%s",
                     "payload":{"family":"publisher","samples":[{"name":"publisher.collection_failures_total",
                     "value":"2","unit":"count","status":"ok","labels":{}}]}}
                    """.formatted(vm, UUID.randomUUID());
            assertThat(zephyrHandler.handle("xenova/v2/vms/" + vm + "/telemetry/metrics",
                    payload.getBytes(StandardCharsets.UTF_8), Instant.now().minusSeconds(1))).isEqualTo(Result.ACCEPTED);
        }
        perform(linux, "/metrics?source=linux_telemetry")
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].source").value("linux_telemetry"))
                .andExpect(jsonPath("$.items[0].values[0].value").value(2.0));
        perform(linux, "/metrics?source=zephyr_telemetry").andExpect(jsonPath("$.items").isEmpty());
        perform(zephyr, "/metrics?source=zephyr_telemetry")
                .andExpect(jsonPath("$.items[0].source").value("zephyr_telemetry"));
    }

    private ObjectNode heartbeat(String vm, String boot, String publisher, String seq, String sent,
            String state, String lastSuccess) throws Exception {
        ObjectNode root = (ObjectNode) objectMapper.readTree(
                Files.readString(Path.of("../platform/guests/zephyr/telemetry/contract/fixtures-2.1/utc.json")));
        root.put("vm_id", vm).put("boot_id", boot).put("publisher_instance_id", publisher).put("seq", seq)
                .put("message_id", String.join("/", vm, boot, publisher, "heartbeat", seq));
        ((ObjectNode) root.path("clock")).put("sent_uptime_ms", sent);
        ((ObjectNode) root.path("payload")).put("publisher_state", state)
                .put("last_collection_success_uptime_ms", lastSuccess)
                .put("collection_failures_total", "18446744073709551615");
        return root;
    }

    private Result publishHeartbeat(ObjectNode root, Instant receivedAt) {
        String topic = "xenova/v2/vms/" + root.path("vm_id").textValue() + "/telemetry/heartbeat";
        return heartbeatHandler.handle(topic, root.toString().getBytes(StandardCharsets.UTF_8), false, receivedAt);
    }

    private Result publishNetwork(String json, Instant received) {
        return agentHandler.handle("xenova/v1/vms/" + ZEPHYR_VM + "/metrics",
                json.getBytes(StandardCharsets.UTF_8), received);
    }

    private String network(String rx, String tx, String epoch, String uptime, String reason, int rc, int seq) {
        ObjectNode root = objectMapper.createObjectNode();
        root.put("schema_version", "1.0").put("type", "agent.metrics").put("message_id", UUID.randomUUID().toString())
                .put("vm_id", ZEPHYR_VM).put("boot_id", "f2000000-0000-4000-8000-000000000001")
                .put("agent_instance_id", "f3000000-0000-4000-8000-000000000001").put("seq", seq).putNull("sent_at");
        ArrayNode values = root.putObject("payload").putNull("window_ms").put("observed_uptime_ms", uptime)
                .putArray("values");
        counter(values, "net_rx_bytes_total", rx, epoch, reason, rc);
        counter(values, "net_tx_bytes_total", tx, epoch, reason, rc);
        return root.toString();
    }

    private void counter(ArrayNode values, String name, String value, String epoch, String reason, int rc) {
        values.addObject().put("name", name).put("unit", "bytes").put("value", value)
                .put("unavailable_reason", reason).put("source_rc", rc).putObject("labels")
                .put("iface", "eth0").put("counter_epoch", epoch);
    }

    private ObjectNode cpuPage(String publisher, String sampleSeq, int part, int rows, String name) throws Exception {
        Path fixture = Path.of("../platform/guests/zephyr/telemetry/contract/fixtures-2.1/cpu.json");
        ObjectNode root = (ObjectNode) objectMapper.readTree(Files.readString(fixture));
        root.put("message_id", UUID.randomUUID().toString()).put("publisher_instance_id", publisher);
        ObjectNode window = (ObjectNode) root.at("/payload/window");
        window.put("sample_seq", sampleSeq).put("part_index", part).put("part_count", (rows + 3) / 4)
                .put("rows_seen", rows).put("rows_returned", rows);
        ArrayNode samples = (ArrayNode) root.at("/payload/samples");
        ObjectNode template = (ObjectNode) samples.remove(1);
        for (int i = part * 4; i < Math.min(rows, (part + 1) * 4); i++) {
            ObjectNode row = template.deepCopy();
            ((ObjectNode) row.get("labels")).put("thread_name", name).put("thread_id", String.valueOf(i + 1));
            samples.add(row);
        }
        return root;
    }

    private Result publishCpu(ObjectNode root, Instant received) {
        return zephyrHandler.handle("xenova/v2/vms/" + ZEPHYR_VM + "/telemetry/metrics",
                root.toString().getBytes(StandardCharsets.UTF_8), received);
    }

    private ResultActions perform(String path) throws Exception {
        return perform(VM, path);
    }

    private ResultActions perform(String vmId, String path) throws Exception {
        return mockMvc.perform(get("/api/v1/vms/" + vmId + path)
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
