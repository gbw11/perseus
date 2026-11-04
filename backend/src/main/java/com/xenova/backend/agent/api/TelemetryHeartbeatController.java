package com.xenova.backend.agent.api;

import com.xenova.backend.agent.application.TelemetryHeartbeatStore;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore.Status;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore.View;
import com.xenova.backend.agent.domain.TelemetryHeartbeat.Report;
import java.math.BigInteger;
import java.time.Instant;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

// The Publisher's own collect/publish state from the v2 telemetry heartbeat, for display only.
// UNKNOWN means no saved report. STALE means the last report is outside the receive lease.
// Treat publisherState as current only while status is FRESH.
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
            BigInteger lastCollectionSuccessAgeMs,
            String lastCollectionSuccessUptimeMs,
            BigInteger collectionFailuresTotal,
            BigInteger publishSubmitFailuresTotal,
            BigInteger connectionAttemptsTotal,
            String bootId,
            String publisherInstanceId,
            Instant receivedAt,
            Long ageMs) {

        static TelemetryHeartbeatResponse of(String vmId, View view) {
            Report heartbeat = view.heartbeat();
            if (heartbeat == null) {
                return new TelemetryHeartbeatResponse(vmId, view.status(), null, null, null, null, null, null,
                        null, null, null, null);
            }
            return new TelemetryHeartbeatResponse(
                    vmId,
                    view.status(),
                    heartbeat.publisherState(),
                    lastSuccessAge(heartbeat),
                    heartbeat.lastCollectionSuccessUptimeMs(),
                    unsigned(heartbeat.collectionFailuresTotal()),
                    unsigned(heartbeat.publishSubmitFailuresTotal()),
                    unsigned(heartbeat.connectionAttemptsTotal()),
                    heartbeat.bootId(),
                    heartbeat.publisherInstanceId(),
                    heartbeat.receivedAt(),
                    view.ageMs());
        }

        private static BigInteger lastSuccessAge(Report report) {
            BigInteger sent = unsigned(report.sentUptimeMs());
            BigInteger success = unsigned(report.lastCollectionSuccessUptimeMs());
            return sent == null || success == null ? null : sent.subtract(success);
        }

        private static BigInteger unsigned(String value) {
            return value == null ? null : new BigInteger(value);
        }
    }
}
