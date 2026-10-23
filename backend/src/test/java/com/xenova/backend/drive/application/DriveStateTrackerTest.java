package com.xenova.backend.drive.application;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

class DriveStateTrackerTest {

    static final String AUTO_VM = "11111111-1111-4111-8111-111111111111";
    static final String MANUAL_VM = "22222222-2222-4222-8222-222222222222";

    private final AtomicLong now = new AtomicLong(1_000_000);
    private final DriveStateTracker tracker = new DriveStateTracker(
            new ObjectMapper(), Clock.fixed(Instant.parse("2026-10-20T00:00:00Z"), ZoneOffset.UTC), now::get);
    private long seq;

    /** A contract §2 envelope. publisher is a vm_id (UUID form) or a source_id. */
    static byte[] envelope(String publisher, String appId, String type, long seq, String payload) {
        boolean vm = publisher.length() == 36;
        String json = """
                {"schema_version":"1.0","message_id":"m-%d","%s":"%s",%s"boot_id":"b-1",
                 "agent_instance_id":"i-1","type":"%s","seq":%d,"sent_at":"2026-10-20T00:00:00.000Z","payload":%s}
                """.formatted(seq, vm ? "vm_id" : "source_id", publisher,
                appId == null ? "" : "\"app_id\":\"" + appId + "\",", type, seq, payload);
        return json.getBytes(StandardCharsets.UTF_8);
    }

    private Optional<DriveNotice> send(String topic, String publisher, String appId, String type, String payload) {
        return tracker.accept(topic, envelope(publisher, appId, type, ++seq, payload));
    }

    private void control(String mode, long generation, String activeApp) {
        send(DriveStateTracker.CONTROL_TOPIC, "dom0-gateway", null, "vehicle.control", """
                {"generation":%d,"mode":"%s","active_app":%s,"active_vm_id":null,"reason":"start"}
                """.formatted(generation, mode, activeApp == null ? "null" : "\"" + activeApp + "\""));
    }

    private void command(String appId, String vm, double v) {
        send("xenova/v1/apps/" + appId + "/events", vm, appId, "drive.command",
                "{\"generation\":1,\"v_mps\":%s,\"steer_rad\":0.1}".formatted(v));
    }

    @Test
    void controlBecomesModeAndActiveApp() {
        control("AUTO", 3, "autonomous");
        DriveState state = tracker.snapshot();
        assertThat(state.control().mode()).isEqualTo("AUTO");
        assertThat(state.control().generation()).isEqualTo(3);
        assertThat(state.control().activeApp()).isEqualTo("autonomous");
    }

    @Test
    void commandIsFreshFor100msThenStale() {
        command("autonomous", AUTO_VM, 0.5);
        now.addAndGet(100);
        assertThat(tracker.snapshot().apps().get("autonomous").commandFresh()).isTrue();
        now.addAndGet(20);
        DriveState.App app = tracker.snapshot().apps().get("autonomous");
        assertThat(app.commandFresh()).isFalse();
        assertThat(app.commandAgoMs()).isEqualTo(120);
        assertThat(app.vmId()).isEqualTo(AUTO_VM);
        assertThat(app.lastCommand().vMps()).isEqualTo(0.5);
    }

    @Test
    void commandRateCountsTheLastSecond() {
        for (int i = 0; i < 50; i++) {
            if (i > 0) {
                now.addAndGet(20); // 50 Hz: 50 commands within 980 ms
            }
            command("manual", MANUAL_VM, 0);
        }
        assertThat(tracker.snapshot().apps().get("manual").commandHz()).isEqualTo(50);
        now.addAndGet(2000);
        assertThat(tracker.snapshot().apps().get("manual").commandHz()).isZero();
    }

    @Test
    void appHeartbeatStateIsKept() {
        send("xenova/v1/apps/manual/events", MANUAL_VM, "manual", "app.heartbeat", "{\"state\":\"standby\",\"uptime_s\":3.0}");
        assertThat(tracker.snapshot().apps().get("manual").heartbeatState()).isEqualTo("standby");
    }

    @Test
    void perceptionIsValidFor200ms() {
        send("xenova/v1/apps/perception/events", "jetson", "perception", "perception.result", """
                {"lane_offset_m":0.05,"heading_err_rad":0.01,"obstacles":[{"x":0,"y":1,"w":0.2}],"stop":false}""");
        now.addAndGet(200);
        DriveState.Perception perception = tracker.snapshot().perception();
        assertThat(perception.valid()).isTrue();
        assertThat(perception.obstacleCount()).isEqualTo(1);
        now.addAndGet(1);
        assertThat(tracker.snapshot().perception().valid()).isFalse();
    }

    @Test
    void vehicleStatusFlagsAreRead() {
        send(DriveStateTracker.STATUS_TOPIC, "esp32", null, "vehicle.status", """
                {"applied_generation":2,"v_mps":0.4,"steer_rad":0.0,"flags":{"estop":false,"watchdog":true,"obstacle":false},"batt_v":7.8}""");
        DriveState.Vehicle vehicle = tracker.snapshot().vehicle();
        assertThat(vehicle.flags().watchdog()).isTrue();
        assertThat(vehicle.battV()).isEqualTo(7.8);
    }

    @Test
    void modeChangeAndFaultBecomeNotices() {
        Optional<DriveNotice> mode = send("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle",
                "drive.mode.changed", "{\"from\":\"AUTO\",\"to\":\"MANUAL\",\"generation\":2,\"reason\":\"command_timeout\"}");
        Optional<DriveNotice> fault = send("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle",
                "drive.fault.detected", "{\"app_id\":\"autonomous\",\"vm_id\":\"%s\",\"reason\":\"command_timeout\"}"
                        .formatted(AUTO_VM));
        assertThat(mode).get().extracting(DriveNotice::type).isEqualTo("drive.mode.changed");
        assertThat(fault).get().extracting(DriveNotice::publisher).isEqualTo("dom0-gateway");
    }

    @Test
    void appIdMustMatchTheTopic() {
        // An autonomous VM publishing as "manual" is rejected (contract §2 APP_ID_MISMATCH)
        tracker.accept("xenova/v1/apps/manual/events", envelope(AUTO_VM, "autonomous", "drive.command", 1, "{}"));
        assertThat(tracker.snapshot().apps()).isEmpty();
        assertThat(tracker.stats().rejected()).isEqualTo(1);
    }

    @Test
    void duplicateOrOlderSeqIsDropped() {
        tracker.accept("xenova/v1/apps/vehicle/events",
                envelope("dom0-gateway", "vehicle", "drive.mode.changed", 5, "{\"to\":\"MANUAL\"}"));
        Optional<DriveNotice> duplicate = tracker.accept("xenova/v1/apps/vehicle/events",
                envelope("dom0-gateway", "vehicle", "drive.mode.changed", 5, "{\"to\":\"MANUAL\"}"));
        assertThat(duplicate).isEmpty();
    }

    @Test
    void badEnvelopesAreCountedNotThrown() {
        tracker.accept(DriveStateTracker.CONTROL_TOPIC, "not json".getBytes(StandardCharsets.UTF_8));
        tracker.accept(DriveStateTracker.CONTROL_TOPIC, "{\"schema_version\":\"2.0\"}".getBytes(StandardCharsets.UTF_8));
        tracker.accept("xenova/v1/elsewhere", envelope("esp32", null, "x", 1, "{}"));
        assertThat(tracker.stats().rejected()).isEqualTo(3);
        assertThat(tracker.snapshot().control()).isNull();
    }

    @Test
    void statsCountPerTopicAndType() {
        command("autonomous", AUTO_VM, 0.5);
        command("autonomous", AUTO_VM, 0.5);
        MessagingStats.Entry entry = tracker.stats().items().get(0);
        assertThat(entry.topic()).isEqualTo("xenova/v1/apps/autonomous/events");
        assertThat(entry.type()).isEqualTo("drive.command");
        assertThat(entry.total()).isEqualTo(2);
    }

    @Test
    void versionChangesOnlyWithNewData() {
        long before = tracker.version();
        tracker.snapshot();
        assertThat(tracker.version()).isEqualTo(before);
        control("AUTO", 1, "autonomous");
        assertThat(tracker.version()).isGreaterThan(before);
    }

    @Test
    void resumeAcceptedStartsACountdownUntilModeChanges() {
        control("AUTO_READY", 4, null);
        Optional<DriveNotice> notice = send("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle",
                "drive.resume.accepted", "{\"switch_in_ms\":3000,\"generation\":4}");
        assertThat(notice).map(DriveNotice::type).contains("drive.resume.accepted");
        now.addAndGet(1200);
        DriveState.Resume resume = tracker.snapshot().resume();
        assertThat(resume.switchInMs()).isEqualTo(3000);
        assertThat(resume.acceptedAgoMs()).isEqualTo(1200);
        assertThat(resume.remainingMs()).isEqualTo(1800);
        assertThat(resume.generation()).isEqualTo(4);

        send("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle", "drive.mode.changed",
                "{\"from\":\"AUTO_READY\",\"to\":\"AUTO\",\"generation\":5,\"reason\":\"resume\"}");
        assertThat(tracker.snapshot().resume()).isNull();
    }

    @Test
    void resumeCancelledOrLeavingAutoReadyClearsTheCountdown() {
        control("AUTO_READY", 4, null);
        send("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle", "drive.resume.accepted",
                "{\"switch_in_ms\":3000,\"generation\":4}");
        Optional<DriveNotice> cancelled = send("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle",
                "drive.resume.cancelled", "{\"reason\":\"obstacle\"}");
        assertThat(cancelled).map(DriveNotice::type).contains("drive.resume.cancelled");
        assertThat(tracker.snapshot().resume()).isNull();

        send("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle", "drive.resume.accepted",
                "{\"switch_in_ms\":3000,\"generation\":4}");
        control("SAFE_STOP", 6, null);
        assertThat(tracker.snapshot().resume()).isNull();

        // A countdown nobody finished disappears shortly after its end.
        control("AUTO_READY", 7, null);
        send("xenova/v1/apps/vehicle/events", "dom0-gateway", "vehicle", "drive.resume.accepted",
                "{\"switch_in_ms\":3000,\"generation\":7}");
        now.addAndGet(3000 + DriveStateTracker.RESUME_GRACE_MS + 1);
        assertThat(tracker.snapshot().resume()).isNull();
    }
}
