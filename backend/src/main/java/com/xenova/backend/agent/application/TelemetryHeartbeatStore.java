package com.xenova.backend.agent.application;

import com.xenova.backend.agent.domain.TelemetryHeartbeat.Report;
import java.time.Clock;
import java.time.Duration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

// Stores the latest diagnostic report and computes its receive lease for the heartbeat API.
@Component
public class TelemetryHeartbeatStore {

    public enum Status { FRESH, STALE, UNKNOWN }

    public record View(Status status, Report heartbeat, Long ageMs) {
    }

    private final Duration lease;
    private final Clock clock;
    private final AgentDataService agentData;

    public TelemetryHeartbeatStore(
            @Value("${xenova.agent.telemetry-heartbeat-lease:6s}") Duration lease, Clock clock,
            AgentDataService agentData) {
        this.lease = lease;
        this.clock = clock;
        this.agentData = agentData;
    }

    public boolean offer(String vmId, Report heartbeat, boolean retained) {
        return !retained && agentData.recordTelemetryHeartbeat(vmId, heartbeat);
    }

    public View view(String vmId) {
        Report heartbeat = agentData.findTelemetryHeartbeat(vmId).orElse(null);
        if (heartbeat == null) {
            return new View(Status.UNKNOWN, null, null);
        }
        Duration age = Duration.between(heartbeat.receivedAt(), clock.instant());
        Status status = age.isNegative() || age.compareTo(lease) > 0 ? Status.STALE : Status.FRESH;
        return new View(status, heartbeat, Math.max(0, age.toMillis()));
    }
}
