package com.xenova.backend.drive.api;

import com.xenova.backend.drive.application.DriveState;
import com.xenova.backend.drive.application.DriveStateTracker;
import com.xenova.backend.drive.application.MessagingStats;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

// Drive relay read APIs (S15P31E110-154). Live updates of the same snapshot go out on WS /ws/v1/drive/state.
@RestController
public class DriveController {

    private final DriveStateTracker tracker;

    public DriveController(DriveStateTracker tracker) {
        this.tracker = tracker;
    }

    // Integrated drive status for the dashboard (UI-09) and the monitor screens.
    @GetMapping("/api/v1/drive/state")
    public DriveState state() {
        return tracker.snapshot();
    }

    // Message counts per topic and type, for checking that every publisher is alive.
    @GetMapping("/api/v1/messaging/stats")
    public MessagingStats stats() {
        return tracker.stats();
    }
}
