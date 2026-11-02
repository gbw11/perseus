package com.xenova.backend.sim.application;

import com.fasterxml.jackson.databind.JsonNode;

// REST-30 GET /api/v1/sim/state body (MQTT contract §12.2): {available, pose, world, pose_age_ms}.
// pose and world are the last sim.pose / sim.world payloads exactly as the simulator sent them (null if none).
// pose_age_ms is measured on the backend's monotonic clock (contract §1).
public record SimState(boolean available, JsonNode pose, JsonNode world, Long poseAgeMs) {
}
