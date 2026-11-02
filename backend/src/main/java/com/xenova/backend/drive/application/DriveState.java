package com.xenova.backend.drive.application;

import com.fasterxml.jackson.annotation.JsonValue;
import java.time.Instant;
import java.util.Map;

// Integrated drive status for the dashboard (UI-09) and the monitor screens. Field names follow the MQTT
// contract §5.4; every "*_ago_ms" is measured on the backend's monotonic clock (contract §1: never compare
// clocks across boards). A null block means nothing has been received yet.
public record DriveState(
        long version,
        Instant observedAt,
        Source source,
        Control control,
        Vehicle vehicle,
        Map<String, App> apps,
        Perception perception,
        Resume resume) {

    public enum SourceStatus {
        FRESH("fresh"),
        STALE("stale"),
        DISCONNECTED("disconnected"),
        UNKNOWN("unknown");

        private final String value;

        SourceStatus(String value) {
            this.value = value;
        }

        @JsonValue
        public String value() {
            return value;
        }
    }

    /** Source freshness is independent from the last values, which stay available for diagnosis. */
    public record Source(SourceStatus status, Long ageMs) {
    }

    // vehicle.control (MQTT-09) from the dom0 gateway.
    public record Control(
            String mode,
            Long generation,
            String activeApp,
            String activeVmId,
            String reason,
            long receivedAgoMs,
            Source source) {
    }

    // vehicle.status (MQTT-10) from the ESP32.
    public record Vehicle(
            Long appliedGeneration,
            Double vMps,
            Double steerRad,
            Double battV,
            Flags flags,
            long receivedAgoMs,
            Source source) {
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
            CommandDirection direction,
            String heartbeatState,
            Long heartbeatAgoMs) {
    }

    public record Command(Long generation, Double vMps, Double steerRad) {
    }

    /**
     * Direction derived only from a fresh drive.command. STOPPED means v_mps=0 and cannot distinguish
     * a brake key from released input; the raw command remains authoritative.
     */
    public record CommandDirection(Longitudinal longitudinal, Lateral lateral) {
    }

    public enum Longitudinal { FORWARD, REVERSE, STOPPED, UNKNOWN }

    public enum Lateral { LEFT, RIGHT, STRAIGHT, UNKNOWN }

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
