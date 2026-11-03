package com.xenova.backend.agent.api;

import com.xenova.backend.agent.application.TelemetryHeartbeatStore;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore.Heartbeat;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore.Status;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore.View;
import java.time.Instant;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

// The Publisher's own collect/publish state from the v2 telemetry heartbeat, for display only.
// UNKNOWN means no heartbeat since the backend started; STALE means none within the lease (6s).
@RestController
public class TelemetryHeartbeatController {

    private final TelemetryHeartbeatStore store;

    public TelemetryHeartbeatController(TelemetryHeartbeatStore store) {
        this.store = store;
    }

    @GetMapping("/api/v1/vms/{vmId}/telemetry/heartbeat")
    public TelemetryHeartbeatResponse heartbeat(@PathVariable String vmId) {
        return TelemetryHeartbeatResponse.of(vmId, store.view(vmId));
    }

    public record TelemetryHeartbeatResponse(
            String vmId,
            Status status,
            String publisherState,
            Long lastCollectionSuccessAgeMs,
            Long collectionFailuresTotal,
            Long publishSubmitFailuresTotal,
            Long connectionAttemptsTotal,
            String bootId,
            Instant receivedAt,
            Long ageMs) {

        static TelemetryHeartbeatResponse of(String vmId, View view) {
            Heartbeat heartbeat = view.heartbeat();
            if (heartbeat == null) {
                return new TelemetryHeartbeatResponse(vmId, view.status(), null, null, null, null, null, null, null,
                        null);
            }
            return new TelemetryHeartbeatResponse(
                    vmId,
                    view.status(),
                    heartbeat.publisherState(),
                    heartbeat.lastCollectionSuccessAgeMs(),
                    heartbeat.collectionFailuresTotal(),
                    heartbeat.publishSubmitFailuresTotal(),
                    heartbeat.connectionAttemptsTotal(),
                    heartbeat.bootId(),
                    heartbeat.receivedAt(),
                    view.ageMs());
        }
    }
}
