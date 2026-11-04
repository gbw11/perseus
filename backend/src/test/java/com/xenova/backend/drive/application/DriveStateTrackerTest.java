package com.xenova.backend.drive.application;

import static org.assertj.core.api.Assertions.assertThat;

import com.fasterxml.jackson.databind.PropertyNamingStrategies;
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

    DriveStateTrackerTest() {
        tracker.mqttConnected();
    }

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
        control(mode, generation, activeApp, null);
    }

    private void control(String mode, long generation, String activeApp, String activeVmId) {
        send(DriveStateTracker.CONTROL_TOPIC, "dom0-gateway", null, "vehicle.control", """
                {"generation":%d,"mode":"%s","active_app":%s,"active_vm_id":%s,"reason":"start"}
                """.formatted(generation, mode, activeApp == null ? "null" : "\"" + activeApp + "\"",
                        activeVmId == null ? "null" : "\"" + activeVmId + "\""));
    }

    private void command(String appId, String vm, double v) {
        command(appId, vm, v, 0.1);
    }

    private void command(String appId, String vm, double v, double steer) {
        send("xenova/v1/apps/" + appId + "/events", vm, appId, "drive.command",
                "{\"generation\":1,\"v_mps\":%s,\"steer_rad\":%s}".formatted(v, steer));
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
    void activeDriveVmFollowsTheReportedIdInEitherDirection() {
        tracker.mqttConnected();
        control("AUTO", 3, "autonomous", AUTO_VM);
        assertThat(tracker.snapshot().control().activeVmId()).isEqualTo(AUTO_VM);
        control("MANUAL", 4, "manual", MANUAL_VM);
        assertThat(tracker.snapshot().control().activeVmId()).isEqualTo(MANUAL_VM);
        assertThat(tracker.snapshot().control().source().status()).isEqualTo(DriveState.SourceStatus.FRESH);
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
        assertThat(app.direction().longitudinal()).isEqualTo(DriveState.Longitudinal.UNKNOWN);
        assertThat(app.direction().lateral()).isEqualTo(DriveState.Lateral.UNKNOWN);
    }

    @Test
    void freshCommandExposesLongitudinalAndLateralDirectionWithoutHidingRawValues() {
        command("manual", MANUAL_VM, -0.3, -0.2);
        DriveState.App app = tracker.snapshot().apps().get("manual");
        assertThat(app.direction().longitudinal()).isEqualTo(DriveState.Longitudinal.REVERSE);
        assertThat(app.direction().lateral()).isEqualTo(DriveState.Lateral.RIGHT);
        assertThat(app.lastCommand().vMps()).isEqualTo(-0.3);
        assertThat(app.lastCommand().steerRad()).isEqualTo(-0.2);

        command("manual", MANUAL_VM, 0, 0);
        assertThat(tracker.snapshot().apps().get("manual").direction())
                .isEqualTo(new DriveState.CommandDirection(
                        DriveState.Longitudinal.STOPPED, DriveState.Lateral.STRAIGHT));
    }

    @Test
    void stateJsonKeepsTheExistingSnakeCaseContractAndAddsSourceAndDirection() throws Exception {
        control("MANUAL", 5, "manual", MANUAL_VM);
        command("manual", MANUAL_VM, 0.3, 0);
        ObjectMapper apiJson = new ObjectMapper()
                .findAndRegisterModules()
                .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);

        String json = apiJson.writeValueAsString(tracker.snapshot());

        assertThat(apiJson.readTree(json).path("control").path("active_vm_id").asText()).isEqualTo(MANUAL_VM);
        assertThat(apiJson.readTree(json).path("control").path("source").path("status").asText())
                .isEqualTo("fresh");
        assertThat(apiJson.readTree(json).path("apps").path("manual").path("direction")
                .path("longitudinal").asText()).isEqualTo("FORWARD");
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
        tracker.mqttConnected();
        send(DriveStateTracker.STATUS_TOPIC, "esp32", null, "vehicle.status", """
                {"applied_generation":2,"v_mps":0.4,"steer_rad":0.0,"flags":{"estop":false,"watchdog":true,"obstacle":false},"batt_v":7.8}""");
        DriveState.Vehicle vehicle = tracker.snapshot().vehicle();
        assertThat(vehicle.flags().watchdog()).isTrue();
        assertThat(vehicle.battV()).isEqualTo(7.8);
        assertThat(vehicle.source().status()).isEqualTo(DriveState.SourceStatus.FRESH);
        now.addAndGet(DriveStateTracker.VEHICLE_FRESH_MS + 1);
        assertThat(tracker.snapshot().vehicle().source().status()).isEqualTo(DriveState.SourceStatus.STALE);
        assertThat(tracker.snapshot().vehicle().vMps()).isEqualTo(0.4);
    }

    @Test
    void mqttLossIsExplicitWithoutReplacingTheLastValues() {
        tracker.mqttConnected();
        control("MANUAL", 5, "manual", MANUAL_VM);
        assertThat(tracker.snapshot().source().status()).isEqualTo(DriveState.SourceStatus.FRESH);
        tracker.mqttDisconnected();
        DriveState state = tracker.snapshot();
        assertThat(state.source().status()).isEqualTo(DriveState.SourceStatus.DISCONNECTED);
        assertThat(state.control().source().status()).isEqualTo(DriveState.SourceStatus.DISCONNECTED);
        assertThat(state.control().mode()).isEqualTo("MANUAL");
        assertThat(state.control().activeVmId()).isEqualTo(MANUAL_VM);
    }

    @Test
    void reconnectDoesNotRejuvenateValuesFromThePreviousConnection() {
        tracker.mqttConnected();
        control("AUTO", 5, "autonomous", AUTO_VM);
        command("manual", MANUAL_VM, 0.3, 0.2);
        assertThat(tracker.snapshot().control().source().status()).isEqualTo(DriveState.SourceStatus.FRESH);
        assertThat(tracker.snapshot().apps().get("manual").commandFresh()).isTrue();

        tracker.mqttDisconnected();
        tracker.mqttConnected();
        DriveState reconnected = tracker.snapshot();
        assertThat(reconnected.control().source().status()).isEqualTo(DriveState.SourceStatus.UNKNOWN);
        assertThat(reconnected.control().activeVmId()).isEqualTo(AUTO_VM);
        assertThat(reconnected.apps().get("manual").commandFresh()).isFalse();
        assertThat(reconnected.apps().get("manual").direction().longitudinal())
                .isEqualTo(DriveState.Longitudinal.UNKNOWN);

        control("MANUAL", 6, "manual", MANUAL_VM);
        assertThat(tracker.snapshot().control().source().status()).isEqualTo(DriveState.SourceStatus.FRESH);
        assertThat(tracker.snapshot().control().activeVmId()).isEqualTo(MANUAL_VM);
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
    void scopedLifecycleAndHeartbeatKeepSeparateFreshnessAndRejectPreviousInstances() {
        String first = "33333333-3333-4333-8333-333333333333";
        String next = "44444444-4444-4444-8444-444444444444";
        String topic = "xenova/v1/vms/" + AUTO_VM + "/apps/autonomous/events";
        String waiting = """
                {"profile":"warm-drive-1","phase":"WAITING","readiness":"QUALIFIED","reason":null,
                 "applied_generation":3,"check_seq":"9","progress_seq":"0"}
                """;
        String idle = "{\"state\":\"idle\",\"uptime_s\":1.5}";
        command("autonomous", MANUAL_VM, 0.5);
        tracker.accept(topic, scoped(AUTO_VM, first, "app.lifecycle", 1, waiting), true);
        tracker.accept(topic, scoped(MANUAL_VM, first, "app.lifecycle", 1, waiting));
        assertThat(tracker.snapshot().vmApps()).isEmpty();

        tracker.accept(topic, scoped(AUTO_VM, first, "app.lifecycle", 1, waiting));
        DriveState.VmApp app = tracker.snapshot().vmApps().get(AUTO_VM).get("autonomous");
        assertThat(app.lifecycle().phase()).isEqualTo("WAITING");
        assertThat(app.lifecycle().progressSeq()).isEqualTo("0");
        assertThat(app.heartbeat()).isNull();
        assertThat(app.heartbeatSource().status()).isEqualTo(DriveState.SourceStatus.UNKNOWN);

        tracker.accept(topic, scoped(AUTO_VM, first, "app.heartbeat", 2, idle));
        tracker.accept("xenova/v1/vms/" + MANUAL_VM + "/apps/autonomous/events",
                scoped(MANUAL_VM, first, "app.heartbeat", 1, idle));
        now.addAndGet(301);
        tracker.accept(topic, scoped(AUTO_VM, first, "app.lifecycle", 3, waiting));
        app = tracker.snapshot().vmApps().get(AUTO_VM).get("autonomous");
        assertThat(app.lifecycleSource().status()).isEqualTo(DriveState.SourceStatus.STALE);
        assertThat(app.heartbeatSource().status()).isEqualTo(DriveState.SourceStatus.FRESH);
        assertThat(tracker.snapshot().vmApps()).containsKeys(AUTO_VM, MANUAL_VM);
        assertThat(tracker.snapshot().apps().get("autonomous").vmId()).isEqualTo(MANUAL_VM);

        tracker.accept(topic, scoped(AUTO_VM, next, "app.heartbeat", 1, idle));
        tracker.accept(topic, scoped(AUTO_VM, first, "app.lifecycle", 4, waiting));
        app = tracker.snapshot().vmApps().get(AUTO_VM).get("autonomous");
        assertThat(app.appInstanceId()).isEqualTo(next);
        assertThat(app.lifecycle()).isNull();
        assertThat(app.heartbeat().state()).isEqualTo("idle");
        tracker.mqttDisconnected();
        tracker.mqttConnected();
        assertThat(tracker.snapshot().vmApps().get(AUTO_VM).get("autonomous").heartbeatSource().status())
                .isEqualTo(DriveState.SourceStatus.UNKNOWN);
    }

    private byte[] scoped(String vm, String instance, String type, int seq, String payload) {
        return new String(envelope(vm, "autonomous", type, seq, payload), StandardCharsets.UTF_8)
                .replace("b-1", "55555555-5555-4555-8555-555555555555").replace("i-1", instance)
                .getBytes(StandardCharsets.UTF_8);
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
