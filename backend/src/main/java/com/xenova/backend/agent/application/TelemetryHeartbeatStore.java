package com.xenova.backend.agent.application;

import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

// Latest v2 telemetry heartbeat per VM (Telemetry contract 2.1). Kept in memory only: it is a diagnostic picture of
// the Publisher's own collect/publish loop, refreshed every 2s, and never a basis for VM health or recovery.
@Component
public class TelemetryHeartbeatStore {

    public enum Status { FRESH, STALE, UNKNOWN }

    public record Heartbeat(
            String vmId,
            String bootId,
            String publisherInstanceId,
            long seq,
            String publisherState,
            Long lastCollectionSuccessAgeMs,
            Long collectionFailuresTotal,
            Long publishSubmitFailuresTotal,
            Long connectionAttemptsTotal,
            Instant receivedAt) {
    }

    public record View(Status status, Heartbeat heartbeat, Long ageMs) {
    }

    private final Map<String, Heartbeat> latest = new ConcurrentHashMap<>();
    private final Duration lease;
    private final Clock clock;

    public TelemetryHeartbeatStore(
            @Value("${xenova.agent.telemetry-heartbeat-lease:6s}") Duration lease, Clock clock) {
        this.lease = lease;
        this.clock = clock;
    }

    // Accepts only a newer seq of the same boot and Publisher instance; a new boot or instance starts over.
    // Retained deliveries are an old value replayed by the broker, so they never refresh the lease.
    public boolean offer(Heartbeat heartbeat, boolean retained) {
        if (retained) {
            return false;
        }
        boolean[] accepted = {false};
        latest.compute(heartbeat.vmId(), (vmId, current) -> {
            if (current != null
                    && current.bootId().equals(heartbeat.bootId())
                    && current.publisherInstanceId().equals(heartbeat.publisherInstanceId())
                    && Long.compareUnsigned(heartbeat.seq(), current.seq()) <= 0) {
                return current;
            }
            accepted[0] = true;
            return heartbeat;
        });
        return accepted[0];
    }

    public View view(String vmId) {
        Heartbeat heartbeat = latest.get(vmId);
        if (heartbeat == null) {
            return new View(Status.UNKNOWN, null, null);
        }
        long ageMs = Math.max(0, Duration.between(heartbeat.receivedAt(), clock.instant()).toMillis());
        Status status = ageMs > lease.toMillis() ? Status.STALE : Status.FRESH;
        return new View(status, heartbeat, ageMs);
    }
}
