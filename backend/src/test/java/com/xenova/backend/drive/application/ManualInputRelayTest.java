package com.xenova.backend.drive.application;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.drive.application.ManualInputRelay.Result;
import java.io.IOException;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

class ManualInputRelayTest {

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final AtomicLong now = new AtomicLong();
    private final DriveStateTracker tracker = new DriveStateTracker(
            objectMapper, Clock.fixed(Instant.parse("2026-10-20T00:00:00Z"), ZoneOffset.UTC), now::get);
    private final List<JsonNode> sent = new ArrayList<>();
    private boolean connected = true;
    private final DriveInputPublisher publisher = (topic, payload, qos) -> {
        assertThat(topic).isEqualTo("xenova/v1/apps/manual/input");
        assertThat(qos).isZero();
        if (!connected) {
            return false;
        }
        try {
            sent.add(objectMapper.readTree(payload));
        } catch (IOException exception) {
            throw new IllegalStateException(exception);
        }
        return true;
    };
    private final ManualInputRelay relay = new ManualInputRelay(
            tracker, () -> publisher, objectMapper, Clock.fixed(Instant.parse("2026-10-20T00:00:00Z"), ZoneOffset.UTC));
    private long seq;

    private void mode(String mode) {
        tracker.accept(DriveStateTracker.CONTROL_TOPIC, DriveStateTrackerTest.envelope(
                "dom0-gateway", null, "vehicle.control", ++seq, "{\"mode\":\"%s\",\"generation\":%d}".formatted(mode, seq)));
    }

    @Test
    void notManualDropsInput() {
        mode("AUTO");
        assertThat(relay.update(List.of("up"))).isEqualTo(Result.NOT_MANUAL);
        relay.repeat();
        assertThat(sent).isEmpty();
        assertThat(relay.held()).isEmpty();
    }

    @Test
    void manualSendsAndRepeatsAt20HzUntilReleased() {
        mode("MANUAL");
        assertThat(relay.update(List.of("up", "left"))).isEqualTo(Result.SENT);
        for (int tick = 0; tick < 5; tick++) {
            relay.repeat(); // the scheduler calls this every 50 ms
        }
        assertThat(relay.update(List.of())).isEqualTo(Result.RELEASED);
        relay.repeat();

        assertThat(sent).hasSize(7); // 1 press + 5 repeats + 1 release, nothing after release
        assertThat(sent.get(0).path("payload").path("keys")).hasSize(2);
        assertThat(sent.get(6).path("payload").path("keys")).isEmpty();
    }

    @Test
    void envelopeFollowsTheContract() {
        mode("MANUAL");
        relay.update(List.of("brake"));
        relay.repeat();
        JsonNode first = sent.get(0);
        assertThat(first.path("schema_version").asText()).isEqualTo("1.0");
        assertThat(first.path("type").asText()).isEqualTo("drive.input");
        assertThat(first.path("source_id").asText()).isEqualTo("dom0-server");
        assertThat(first.has("vm_id")).isFalse();
        assertThat(first.path("seq").asLong()).isEqualTo(1);
        assertThat(sent.get(1).path("seq").asLong()).isEqualTo(2);
        assertThat(first.path("boot_id").asText()).isNotBlank();
        assertThat(first.path("sent_at").asText()).isEqualTo("2026-10-20T00:00:00Z");
    }

    @Test
    void unknownKeysAreIgnored() {
        mode("MANUAL");
        relay.update(List.of("ArrowUp", "w", "up", "jump"));
        assertThat(relay.held()).containsExactly("up");
    }

    @Test
    void losingManualStopsTheRepeat() {
        mode("MANUAL");
        relay.update(List.of("up"));
        mode("AUTO_READY");
        relay.repeat();
        assertThat(sent).hasSize(1);
        assertThat(relay.held()).isEmpty();
    }

    @Test
    void releaseWithoutHeldKeysSendsNothing() {
        mode("MANUAL");
        relay.release();
        assertThat(sent).isEmpty();
    }

    @Test
    void brokerDownIsReported() {
        mode("MANUAL");
        connected = false;
        assertThat(relay.update(List.of("up"))).isEqualTo(Result.NOT_CONNECTED);
    }
}
