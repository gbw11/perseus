package com.xenova.backend.drive.application;

import java.time.Instant;
import java.util.Map;

// Integrated drive status for the dashboard (UI-09) and the monitor screens. Field names follow the MQTT
// contract §5.4; every "*_ago_ms" is measured on the backend's monotonic clock (contract §1: never compare
// clocks across boards). A null block means nothing has been received yet.
public record DriveState(
        long version,
        Instant observedAt,
        Control control,
        Vehicle vehicle,
        Map<String, App> apps,
        Perception perception,
        Resume resume) {

    // vehicle.control (MQTT-09) from the dom0 gateway.
    public record Control(
            String mode,
            Long generation,
            String activeApp,
            String activeVmId,
            String reason,
            long receivedAgoMs) {
    }

    // vehicle.status (MQTT-10) from the ESP32.
    public record Vehicle(
            Long appliedGeneration,
            Double vMps,
            Double steerRad,
            Double battV,
            Flags flags,
            long receivedAgoMs) {
    }

    public record Flags(boolean estop, boolean watchdog, boolean obstacle) {
    }

    // One driving or interaction app, keyed by app_id. command_fresh mirrors the gateway's 100 ms rule.
    public record App(
            String vmId,
            Boolean commandFresh,
            Long commandAgoMs,
            int commandHz,
            Command lastCommand,
            String heartbeatState,
            Long heartbeatAgoMs) {
    }

    public record Command(Long generation, Double vMps, Double steerRad) {
    }

    // perception.result (MQTT-06) from the Jetson.
    public record Perception(
            boolean valid,
            long receivedAgoMs,
            int hz,
            Double laneOffsetM,
            Double headingErrRad,
            int obstacleCount,
            boolean stop) {
    }

    // A resume countdown the gateway accepted (drive.resume.accepted) and that has not ended yet: the switch to
    // AUTO is expected remaining_ms from now. Null when there is none, after drive.resume.cancelled, after any
    // drive.mode.changed, or once vehicle.control leaves AUTO_READY.
    public record Resume(
            Long generation,
            long switchInMs,
            long acceptedAgoMs,
            long remainingMs) {
    }
}
