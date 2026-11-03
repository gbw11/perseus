package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;

import com.xenova.backend.agent.application.AgentMessageHandler.Result;
import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricSampleRepository;
import com.xenova.backend.agent.domain.MetricValue;
import com.xenova.backend.agent.domain.ProcessSnapshotRepository;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest
class ZephyrTelemetryMessageHandlerIntegrationTest {

    // The contract's own 2.1 fixtures, so this test breaks when the wire format changes.
    private static final Path FIXTURES = Path.of("..", "platform", "guests", "zephyr", "telemetry", "contract",
            "fixtures-2.1");
    private static final String VM = "f1000000-0000-4000-8000-000000000001";
    private static final String TOPIC = "xenova/v2/vms/" + VM + "/telemetry/metrics";
    private static final Instant RECEIVED = Instant.parse("2026-11-02T03:00:01Z");

    @Autowired
    private VmTelemetryMessageHandler handler;

    @Autowired
    private MetricSampleRepository metricSampleRepository;

    @Autowired
    private ProcessSnapshotRepository processSnapshotRepository;

    @BeforeEach
    void cleanUp() {
        metricSampleRepository.deleteAll();
        processSnapshotRepository.deleteAll();
    }

    @Test
    void storesCpuFamilyOnceWithArrivalTimeWhenUtcIsUnavailable() throws IOException {
        byte[] cpu = fixture("cpu.json");

        assertThat(handler.handle(TOPIC, cpu, RECEIVED)).isEqualTo(Result.ACCEPTED);
        assertThat(handler.handle(TOPIC, cpu, RECEIVED)).isEqualTo(Result.IGNORED);

        List<MetricSample> samples = metricSampleRepository.findAll();
        assertThat(samples).hasSize(1);
        MetricSample sample = samples.getFirst();
        assertThat(sample.getVmId()).isEqualTo(VM);
        assertThat(sample.getSource()).isEqualTo("zephyr_telemetry");
        assertThat(sample.getBootId()).isEqualTo("f2000000-0000-4000-8000-000000000001");
        assertThat(sample.getObservedAt()).isEqualTo(RECEIVED);
        assertThat(sample.getWindowMs()).isEqualTo(1000);
        assertThat(value(sample, "cpu.system.percent").value()).isEqualTo(25.25);
        assertThat(value(sample, "cpu.system.percent").unit()).isEqualTo("percent");
        assertThat(processSnapshotRepository.count()).isZero();
    }

    @Test
    void convertsDecimalStringsAndKeepsLabels() throws IOException {
        assertThat(handler.handle(TOPIC, fixture("memory_regions.json"), RECEIVED)).isEqualTo(Result.ACCEPTED);

        MetricSample sample = metricSampleRepository.findAll().getFirst();
        MetricValue used = value(sample, "memory.region.used_bytes");
        assertThat(used.value()).isEqualTo(256.0);
        assertThat(used.labels()).containsEntry("region", "net_rx_data");
        assertThat(value(sample, "memory.region.capacity_bytes").value()).isEqualTo(1024.0);
    }

    @Test
    void keepsUnavailableSamplesAsNullWithReason() {
        String json = message("memory", """
                [{"name":"memory.kernel_heap.used_bytes","unit":"bytes","kind":"gauge","encoding":"u64_decimal",
                  "value":null,"status":"unavailable","reason":"not_registered","source_rc":-2,"labels":{}}]
                """);

        assertThat(handler.handle(TOPIC, bytes(json), RECEIVED)).isEqualTo(Result.ACCEPTED);

        MetricValue heap = value(metricSampleRepository.findAll().getFirst(), "memory.kernel_heap.used_bytes");
        assertThat(heap.value()).isNull();
        assertThat(heap.unavailableReason()).isEqualTo("not_registered");
    }

    @Test
    void rejectsMessagesThatBreakTheContract() throws IOException {
        String cpu = Files.readString(FIXTURES.resolve("cpu.json"));

        assertThat(handler.handle(TOPIC.replace(VM, "other-vm"), bytes(cpu), RECEIVED)).isEqualTo(Result.REJECTED);
        assertThat(handler.handle(TOPIC, bytes(cpu.replace("\"2.1\"", "\"2.0\"")), RECEIVED))
                .isEqualTo(Result.REJECTED);
        assertThat(handler.handle(TOPIC, bytes(cpu.replace("telemetry.metrics", "telemetry.heartbeat")), RECEIVED))
                .isEqualTo(Result.REJECTED);
        assertThat(handler.handle(TOPIC, bytes("not json"), RECEIVED)).isEqualTo(Result.REJECTED);
        assertThat(handler.handle(TOPIC, new byte[AgentMessageHandler.MAX_PAYLOAD_BYTES + 1], RECEIVED))
                .isEqualTo(Result.REJECTED);

        assertThat(metricSampleRepository.count()).isZero();
    }

    private static String message(String family, String samples) {
        return """
                {"schema_version":"2.1","type":"telemetry.metrics","message_id":"%s/b/p/metrics/1","vm_id":"%s",
                 "boot_id":"b","seq":"1","clock":{"sent_uptime_ms":"10","sent_at":null},
                 "payload":{"family":"%s","window":null,"samples":%s}}
                """.formatted(VM, VM, family, samples);
    }

    private static MetricValue value(MetricSample sample, String name) {
        return sample.getValues().stream().filter(value -> value.name().equals(name)).findFirst().orElseThrow();
    }

    private static byte[] fixture(String name) throws IOException {
        return Files.readAllBytes(FIXTURES.resolve(name));
    }

    private static byte[] bytes(String json) {
        return json.getBytes(StandardCharsets.UTF_8);
    }
}
