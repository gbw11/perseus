package com.xenova.backend.sim.application;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

class SimStateTrackerTest {

    private final AtomicLong now = new AtomicLong(1_000_000);
    private final SimStateTracker tracker = new SimStateTracker(new ObjectMapper(), now::get);
    private long seq;

    private byte[] envelope(String type, String payload) {
        return """
                {"schema_version":"1.0","message_id":"m-%d","source_id":"simulator","boot_id":"b-1",
                 "agent_instance_id":"i-1","type":"%s","seq":%d,"sent_at":"2026-10-29T00:00:00.000Z","payload":%s}
                """.formatted(++seq, type, seq, payload).getBytes(StandardCharsets.UTF_8);
    }

    private void pose(double x) {
        tracker.accept(SimStateTracker.POSE_TOPIC, envelope("sim.pose",
                "{\"x\":%s,\"y\":-5.6,\"yaw\":3.4,\"v_mps\":0.5,\"steer_rad\":0.1,\"world_revision\":1,\"surface\":\"road\"}"
                        .formatted(x)));
    }

    private void world(long revision) {
        tracker.accept(SimStateTracker.WORLD_TOPIC, envelope("sim.world",
                "{\"revision\":%d,\"track\":{\"id\":\"spielberg\",\"centerline\":[[0,0],[1,0]]},\"obstacles\":[],\"estop\":false}"
                        .formatted(revision)));
    }

    @Test
    void nothingReceivedIsUnavailable() {
        SimState state = tracker.snapshot();
        assertThat(state.available()).isFalse();
        assertThat(state.pose()).isNull();
        assertThat(state.world()).isNull();
        assertThat(state.poseAgeMs()).isNull();
    }

    @Test
    void poseWithinOneSecondIsAvailableAndPassedThroughUnchanged() {
        pose(6.5);
        now.addAndGet(1000);
        SimState state = tracker.snapshot();
        assertThat(state.available()).isTrue();
        assertThat(state.poseAgeMs()).isEqualTo(1000);
        assertThat(state.pose().path("x").asDouble()).isEqualTo(6.5);
        assertThat(state.pose().path("surface").asText()).isEqualTo("road"); // v1.4 optional field kept as is
        now.addAndGet(1);
        assertThat(tracker.snapshot().available()).isFalse();
    }

    @Test
    void worldIsKeptAndSmallerRevisionMeansNewRun() {
        AtomicInteger changes = new AtomicInteger();
        tracker.onWorldChange(changes::incrementAndGet);
        world(5);
        world(1); // simulator restarted
        assertThat(tracker.snapshot().world().path("revision").asLong()).isEqualTo(1);
        assertThat(changes.get()).isEqualTo(2);
    }

    @Test
    void clearedRetainedWorldBecomesNull() {
        world(3);
        tracker.accept(SimStateTracker.WORLD_TOPIC, new byte[0]);
        assertThat(tracker.snapshot().world()).isNull();
    }

    @Test
    void commandResultGoesToListeners() {
        List<JsonNode> results = new ArrayList<>();
        tracker.onResult(results::add);
        tracker.accept(SimStateTracker.RESULT_TOPIC, envelope("sim.command.result",
                "{\"request_id\":\"r-1\",\"op\":\"obstacle.add\",\"ok\":false,\"code\":\"NO_GAP\",\"message\":\"틈 없음\"}"));
        assertThat(results).hasSize(1);
        assertThat(results.get(0).path("code").asText()).isEqualTo("NO_GAP");
    }

    @Test
    void badMessagesAreDroppedAndCounted() {
        tracker.accept(SimStateTracker.POSE_TOPIC, "not json".getBytes(StandardCharsets.UTF_8));
        tracker.accept(SimStateTracker.POSE_TOPIC, envelope("sim.world", "{\"x\":1,\"y\":2,\"yaw\":0}"));
        tracker.accept(SimStateTracker.POSE_TOPIC, envelope("sim.pose", "{\"x\":1}"));
        tracker.accept(SimStateTracker.WORLD_TOPIC, envelope("sim.world", "{\"revision\":1}"));
        tracker.accept("xenova/v1/sim/other", envelope("sim.pose", "{\"x\":1,\"y\":2,\"yaw\":0}"));
        assertThat(tracker.rejected()).isEqualTo(5);
        assertThat(tracker.snapshot().pose()).isNull();
        assertThat(tracker.snapshot().world()).isNull();
    }
}
