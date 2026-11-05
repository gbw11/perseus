package com.xenova.backend.drive.api;

import com.xenova.backend.drive.application.DriveResumeRequester;
import com.xenova.backend.drive.application.DriveState;
import com.xenova.backend.drive.application.DriveStateTracker;
import com.xenova.backend.drive.application.MessagingStats;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RestController;

// Drive relay read APIs (S15P31E110-154). Live updates of the same snapshot go out on WS /ws/v1/drive/state.
@RestController
public class DriveController {

    private final DriveStateTracker tracker;
    private final DriveResumeRequester resumeRequester;

    public DriveController(DriveStateTracker tracker, DriveResumeRequester resumeRequester) {
        this.tracker = tracker;
        this.resumeRequester = resumeRequester;
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

    // Web "resume autonomous driving" (MQTT-13). 202: the gateway decides; the countdown arrives as
    // drive.resume.accepted (event + DriveState.resume), a refusal as drive.resume.cancelled.
    @PostMapping("/api/v1/drive/resume")
    public ResponseEntity<Map<String, Object>> resume(Authentication authentication) {
        resumeRequester.request(username(authentication));
        return ResponseEntity.status(HttpStatus.ACCEPTED).body(Map.of("accepted", true));
    }

    // Cancel the resume countdown (contract v1.7). 202: the gateway answers with drive.resume.cancelled
    // (reason operator_cancelled) and the mode stays; 409 NO_RESUME_COUNTDOWN when there is none.
    @DeleteMapping("/api/v1/drive/resume")
    public ResponseEntity<Map<String, Object>> cancelResume(Authentication authentication) {
        resumeRequester.cancel(username(authentication));
        return ResponseEntity.status(HttpStatus.ACCEPTED).body(Map.of("accepted", true));
    }

    // The JWT carries the login name as the "username" claim; the principal itself is the user id.
    private static String username(Authentication authentication) {
        if (authentication == null) {
            return null;
        }
        if (authentication.getDetails() instanceof Jwt jwt && jwt.getClaimAsString("username") != null) {
            return jwt.getClaimAsString("username");
        }
        return authentication.getName();
    }
}
