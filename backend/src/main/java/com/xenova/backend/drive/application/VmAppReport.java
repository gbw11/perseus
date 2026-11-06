package com.xenova.backend.drive.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.xenova.backend.common.UnsignedDecimal;
import java.util.Set;

/** Interprets scoped app reports for observation only, using the v1 MQTT payload contract. */
final class VmAppReport {
    private static final Set<String> PHASES = Set.of("WAITING", "ACTIVATING", "EXECUTING", "STOPPING", "RESIDUAL");
    private static final Set<String> REASONS = Set.of("CONTROL_UNAPPLIED", "INPUT_UNAVAILABLE", "INPUT_STALE",
            "INPUT_INVALID", "STATE_PREPARING", "CALCULATION_FAILED", "OUTPUT_INVALID",
            "APP_STOPPING", "RESOURCE_RETAINED");
    private static final Set<String> HEARTBEAT_STATES = Set.of("active", "standby", "idle", "error");

    private VmAppReport() {
    }

    static DriveState.Lifecycle lifecycle(JsonNode payload) {
        String profile = payload.path("profile").asText("");
        String phase = payload.path("phase").asText("");
        String readiness = payload.path("readiness").asText("");
        JsonNode reason = payload.path("reason");
        JsonNode generation = payload.path("applied_generation");
        String check = UnsignedDecimal.read(payload.path("check_seq"), "check_seq");
        String progress = UnsignedDecimal.read(payload.path("progress_seq"), "progress_seq");
        long applied = generation.asLong();
        boolean validReadiness = switch (readiness) {
            case "QUALIFIED" -> "WAITING".equals(phase) && applied > 0 && reason.isNull();
            case "READY" -> "EXECUTING".equals(phase) && applied > 0 && !"0".equals(progress) && reason.isNull();
            case "NOT_READY" -> reason.isTextual() && REASONS.contains(reason.textValue());
            default -> false;
        };
        if (payload.size() != 7 || !"warm-drive-1".equals(profile) || !PHASES.contains(phase)
                || !generation.isIntegralNumber() || !generation.canConvertToLong()
                || applied < 0 || applied > 0xffff_ffffL || "0".equals(check) || !validReadiness) {
            throw new IllegalArgumentException("invalid app.lifecycle payload");
        }
        return new DriveState.Lifecycle(profile, phase, readiness, reason.textValue(), applied, check, progress);
    }

    static DriveState.Heartbeat heartbeat(JsonNode payload) {
        String state = payload.path("state").asText("");
        JsonNode uptime = payload.path("uptime_s");
        if (!HEARTBEAT_STATES.contains(state) || !uptime.isNumber()
                || !Double.isFinite(uptime.asDouble()) || uptime.asDouble() < 0) {
            throw new IllegalArgumentException("invalid app.heartbeat payload");
        }
        return new DriveState.Heartbeat(state, uptime.asDouble());
    }
}
