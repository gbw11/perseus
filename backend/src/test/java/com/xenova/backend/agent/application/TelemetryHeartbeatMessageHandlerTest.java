package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.application.AgentMessageHandler.Result;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore.Heartbeat;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore.Status;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore.View;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class TelemetryHeartbeatMessageHandlerTest {

    private static final String VM = "f1000000-0000-4000-8000-000000000001";
    private static final String OTHER_VM = "f1000000-0000-4000-8000-000000000002";
    private static final String TOPIC = "xenova/v2/vms/" + VM + "/telemetry/heartbeat";
    private static final String BOOT = "f2000000-0000-4000-8000-000000000001";
    private static final String INSTANCE = "f3000000-0000-4000-8000-000000000001";
    private static final Instant T0 = Instant.parse("2026-11-03T08:00:00Z");

    private final AtomicReference<Instant> now = new AtomicReference<>(T0);
    private TelemetryHeartbeatStore store;
    private TelemetryHeartbeatMessageHandler handler;

    @BeforeEach
    void setUp() {
        Clock clock = new Clock() {
            @Override
            public ZoneId getZone() {
                return ZoneId.of("UTC");
            }

            @Override
            public Clock withZone(ZoneId zone) {
                return this;
            }

            @Override
            public Instant instant() {
                return now.get();
            }
        };
        store = new TelemetryHeartbeatStore(Duration.ofSeconds(6), clock);
        handler = new TelemetryHeartbeatMessageHandler(new ObjectMapper(), store);
    }

    @Test
    void keepsPublisherStateAndLastSuccessAge() {
        assertThat(handler.handle(TOPIC, heartbeat(VM, BOOT, "7", "degraded", "12000", "10500"), false, T0))
                .isEqualTo(Result.ACCEPTED);

        View view = store.view(VM);
        assertThat(view.status()).isEqualTo(Status.FRESH);
        Heartbeat kept = view.heartbeat();
        assertThat(kept.publisherState()).isEqualTo("degraded");
        assertThat(kept.lastCollectionSuccessAgeMs()).isEqualTo(1500L);
        assertThat(kept.collectionFailuresTotal()).isEqualTo(3L);
        assertThat(kept.publishSubmitFailuresTotal()).isZero();
        assertThat(kept.connectionAttemptsTotal()).isEqualTo(1L);
        assertThat(kept.bootId()).isEqualTo(BOOT);
        assertThat(view.ageMs()).isZero();
    }

    @Test
    void becomesStaleAfterLeaseAndUnknownWithoutHeartbeat() {
        assertThat(store.view(VM).status()).isEqualTo(Status.UNKNOWN);
        handler.handle(TOPIC, heartbeat(VM, BOOT, "1", "running", "2000", "2000"), false, T0);

        now.set(T0.plusSeconds(6));
        assertThat(store.view(VM).status()).isEqualTo(Status.FRESH);
        now.set(T0.plusMillis(6001));
        assertThat(store.view(VM).status()).isEqualTo(Status.STALE);
        assertThat(store.view(VM).ageMs()).isEqualTo(6001L);
    }

    @Test
    void ignoresDuplicateOlderAndRetainedDelivery() {
        handler.handle(TOPIC, heartbeat(VM, BOOT, "5", "running", "2000", "2000"), false, T0);

        assertThat(handler.handle(TOPIC, heartbeat(VM, BOOT, "5", "degraded", "2000", "2000"), false,
                T0.plusSeconds(1))).isEqualTo(Result.IGNORED);
        assertThat(handler.handle(TOPIC, heartbeat(VM, BOOT, "4", "degraded", "2000", "2000"), false,
                T0.plusSeconds(1))).isEqualTo(Result.IGNORED);
        assertThat(handler.handle(TOPIC, heartbeat(VM, BOOT, "9", "degraded", "2000", "2000"), true,
                T0.plusSeconds(1))).isEqualTo(Result.IGNORED);

        Heartbeat kept = store.view(VM).heartbeat();
        assertThat(kept.seq()).isEqualTo(5L);
        assertThat(kept.receivedAt()).isEqualTo(T0);
    }

    @Test
    void newBootStartsOverEvenWithLowerSeq() {
        handler.handle(TOPIC, heartbeat(VM, BOOT, "50", "running", "2000", "2000"), false, T0);
        String newBoot = "f2000000-0000-4000-8000-000000000009";

        assertThat(handler.handle(TOPIC, heartbeat(VM, newBoot, "1", "running", "2000", "2000"), false,
                T0.plusSeconds(2))).isEqualTo(Result.ACCEPTED);
        assertThat(store.view(VM).heartbeat().bootId()).isEqualTo(newBoot);
    }

    @Test
    void rejectsMismatchedVmWrongSchemaAndUnknownState() {
        assertThat(handler.handle(TOPIC, heartbeat(OTHER_VM, BOOT, "1", "running", "2000", "2000"), false, T0))
                .isEqualTo(Result.REJECTED);
        assertThat(handler.handle(TOPIC, replace(heartbeat(VM, BOOT, "2", "running", "2000", "2000"),
                "\"2.1\"", "\"2.0\""), false, T0)).isEqualTo(Result.REJECTED);
        assertThat(handler.handle(TOPIC, heartbeat(VM, BOOT, "3", "sleeping", "2000", "2000"), false, T0))
                .isEqualTo(Result.REJECTED);
        assertThat(handler.handle(TOPIC, "not json".getBytes(StandardCharsets.UTF_8), false, T0))
                .isEqualTo(Result.REJECTED);
        assertThat(handler.handle("xenova/v2/vms/" + VM + "/telemetry/metrics",
                heartbeat(VM, BOOT, "4", "running", "2000", "2000"), false, T0)).isEqualTo(Result.REJECTED);
    }

    @Test
    void leavesLastSuccessAgeEmptyWhenUptimeIsMissingOrAhead() {
        handler.handle(TOPIC, heartbeat(VM, BOOT, "1", "running", "2000", "3000"), false, T0);

        assertThat(store.view(VM).heartbeat().lastCollectionSuccessAgeMs()).isNull();
    }

    private static byte[] replace(byte[] payload, String from, String to) {
        return new String(payload, StandardCharsets.UTF_8).replace(from, to).getBytes(StandardCharsets.UTF_8);
    }

    // Shape of the Linux agent's v2 heartbeat (vm-agent README, Telemetry contract 2.1).
    private static byte[] heartbeat(String vmId, String bootId, String seq, String state, String sentUptimeMs,
            String lastSuccessUptimeMs) {
        String json = """
                {"schema_version":"2.1","type":"telemetry.heartbeat",
                 "message_id":"%1$s/%2$s/%3$s/heartbeat/%4$s",
                 "vm_id":"%1$s","boot_id":"%2$s","publisher_instance_id":"%3$s","seq":"%4$s",
                 "context":{"binding":"unbound"},
                 "clock":{"sent_uptime_ms":"%6$s","sent_at":null,"utc_quality":"unavailable"},
                 "payload":{"publisher_state":"%5$s","last_collection_success_uptime_ms":"%7$s",
                  "collection_failures_total":"3","connection_attempts_total":"1",
                  "publish_submit_failures_total":"0","app_health_asserted":false}}
                """.formatted(vmId, bootId, INSTANCE, seq, state, sentUptimeMs, lastSuccessUptimeMs);
        return json.getBytes(StandardCharsets.UTF_8);
    }
}
