package com.xenova.backend.display.application;

import java.time.Instant;
import java.util.List;

// One monitor as REST-23 / WS /ws/v1/displays show it (S15P31E110-129). desired_route is what the console asked
// for; observed_route is what the monitor browser last confirmed with route.applied (null until then).
public record Display(
        String displayId,
        String label,
        boolean connected,
        DisplayStatus status,
        List<String> supportedViews,
        long revision,
        Route desiredRoute,
        Route observedRoute,
        String lastError) {

    // vm_id is null for the split view. applied_at and verification_level are set only on observed_route.
    public record Route(
            String vmId,
            String view,
            String requestId,
            Instant appliedAt,
            String verificationLevel) {
    }
}
