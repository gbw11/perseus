package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;

import com.xenova.backend.agent.application.AgentMessageHandler.Result;
import com.xenova.backend.agent.domain.AgentHeartbeat;
import com.xenova.backend.agent.domain.AgentHeartbeatRepository;
import com.xenova.backend.agent.domain.AgentInventory;
import com.xenova.backend.agent.domain.AgentInventoryRepository;
import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricSampleRepository;
import com.xenova.backend.agent.domain.ProcessSnapshotRepository;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest
class AgentMessageHandlerIntegrationTest {

    private static final String VM = "550e8400-e29b-41d4-a716-446655440000";
    private static final Instant RECEIVED = Instant.parse("2026-10-16T03:00:01Z");

    @Autowired
    private AgentMessageHandler handler;

    @Autowired
    private AgentHeartbeatRepository heartbeatRepository;

    @Autowired
    private AgentInventoryRepository inventoryRepository;

    @Autowired
    private MetricSampleRepository metricSampleRepository;

    @Autowired
    private ProcessSnapshotRepository processSnapshotRepository;

    @BeforeEach
    void cleanUp() {
        heartbeatRepository.deleteAll();
        inventoryRepository.deleteAll();
        metricSampleRepository.deleteAll();
        processSnapshotRepository.deleteAll();
    }

    @Test
    void storesHeartbeatAndIgnoresDuplicateOrOlderSequence() {
        assertThat(handle("heartbeat", heartbeat("m-1", 5, "2026-10-16T03:00:00Z"))).isEqualTo(Result.ACCEPTED);
        assertThat(handle("heartbeat", heartbeat("m-1", 5, "2026-10-16T03:00:00Z"))).isEqualTo(Result.IGNORED);
        assertThat(handle("heartbeat", heartbeat("m-0", 4, "2026-10-16T02:59:55Z"))).isEqualTo(Result.IGNORED);

        AgentHeartbeat stored = heartbeatRepository.findById(VM).orElseThrow();
        assertThat(stored.getSeq()).isEqualTo(5L);
        assertThat(stored.getBootId()).isEqualTo("boot-1");
        assertThat(stored.getReceivedAt()).isEqualTo(RECEIVED);
    }

    @Test
    void storesInventoryWithInterfaceAddresses() {
        String payload = """
                {"os":{"id":"debian","version_id":"12","pretty_name":"Debian GNU/Linux 12 (bookworm)"},
                 "kernel_release":"6.6.31","hostname":"debian-01",
                 "interfaces":[{"name":"eth0","mac":"00:16:3e:00:00:01",
                   "addresses":[{"address":"10.0.0.11","family":"ipv4"}]}],
                 "agent_version":"0.1.0","capabilities":{"commands":[],"metrics":[]}}
                """;

        assertThat(handle("inventory", envelope("agent.inventory", "m-1", 1, "2026-10-16T03:00:00Z", payload)))
                .isEqualTo(Result.ACCEPTED);

        AgentInventory stored = inventoryRepository.findById(VM).orElseThrow();
        assertThat(stored.getOsId()).isEqualTo("debian");
        assertThat(stored.getPrettyName()).isEqualTo("Debian GNU/Linux 12 (bookworm)");
        assertThat(stored.getInterfaces().getFirst().ips()).containsExactly("10.0.0.11");
        assertThat(stored.getAgentVersion()).isEqualTo("0.1.0");
    }

    @Test
    void storesMetricsOnceAndKeepsLatestProcesses() {
        String payload = """
                {"window_ms":2000,
                 "values":[{"name":"cpu_percent_allocated","value":20,"unit":"percent","labels":{},"unavailable_reason":null},
                           {"name":"net_rx_bytes_total","value":null,"unit":"bytes","labels":{"iface":"eth0"},
                            "unavailable_reason":"not supported"}],
                 "processes":[{"pid":42,"name":"stress","cpu_percent":95.5,"rss_bytes":1048576}]}
                """;
        byte[] message = envelope("agent.metrics", "m-1", 1, "2026-10-16T03:00:00Z", payload);

        assertThat(handle("metrics", message)).isEqualTo(Result.ACCEPTED);
        assertThat(handle("metrics", message)).isEqualTo(Result.IGNORED);

        MetricSample sample = metricSampleRepository.findById("m-1").orElseThrow();
        assertThat(sample.getSource()).isEqualTo("guest_agent");
        assertThat(sample.getValues()).hasSize(2);
        assertThat(sample.getValues().get(1).value()).isNull();
        assertThat(sample.getValues().get(1).labels()).containsEntry("iface", "eth0");
        assertThat(processSnapshotRepository.findById(VM).orElseThrow().getProcesses().getFirst().name())
                .isEqualTo("stress");
    }

    @Test
    void rejectsMessagesThatBreakTheContract() {
        String otherVm = heartbeat("m-1", 1, "2026-10-16T03:00:00Z").replace(VM, "other-vm");
        assertThat(handle("heartbeat", otherVm)).isEqualTo(Result.REJECTED);

        String oldSchema = heartbeat("m-2", 1, "2026-10-16T03:00:00Z").replace("\"1.0\"", "\"0.9\"");
        assertThat(handle("heartbeat", oldSchema)).isEqualTo(Result.REJECTED);

        assertThat(handle("inventory", heartbeat("m-3", 1, "2026-10-16T03:00:00Z"))).isEqualTo(Result.REJECTED);

        String badUnit = """
                {"window_ms":2000,"values":[{"name":"cpu","value":1,"unit":"cores","labels":{}}],"processes":[]}
                """;
        assertThat(handle("metrics", envelope("agent.metrics", "m-4", 1, "2026-10-16T03:00:00Z", badUnit)))
                .isEqualTo(Result.REJECTED);

        assertThat(handler.handle("xenova/v1/vms/" + VM + "/heartbeat", "not json".getBytes(StandardCharsets.UTF_8),
                RECEIVED)).isEqualTo(Result.REJECTED);
        assertThat(handler.handle("xenova/v1/vms/" + VM + "/heartbeat",
                new byte[AgentMessageHandler.MAX_PAYLOAD_BYTES + 1], RECEIVED)).isEqualTo(Result.REJECTED);

        assertThat(heartbeatRepository.count()).isZero();
        assertThat(metricSampleRepository.count()).isZero();
    }

    private Result handle(String kind, String json) {
        return handle(kind, json.getBytes(StandardCharsets.UTF_8));
    }

    private Result handle(String kind, byte[] json) {
        return handler.handle("xenova/v1/vms/" + VM + "/" + kind, json, RECEIVED);
    }

    private String heartbeat(String messageId, long seq, String sentAt) {
        return new String(envelope("agent.heartbeat", messageId, seq, sentAt,
                "{\"uptime_ms\":20000,\"agent_version\":\"0.1.0\"}"), StandardCharsets.UTF_8);
    }

    private byte[] envelope(String type, String messageId, long seq, String sentAt, String payload) {
        return """
                {"schema_version":"1.0","message_id":"%s","vm_id":"%s","boot_id":"boot-1",
                 "agent_instance_id":"agent-1","type":"%s","seq":%d,"sent_at":"%s","payload":%s}
                """.formatted(messageId, VM, type, seq, sentAt, payload).getBytes(StandardCharsets.UTF_8);
    }
}
