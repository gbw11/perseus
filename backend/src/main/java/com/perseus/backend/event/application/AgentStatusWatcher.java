package com.perseus.backend.event.application;

import com.perseus.backend.agent.application.AgentDataService;
import com.perseus.backend.agent.domain.AgentHeartbeat;
import com.perseus.backend.agent.domain.AgentHeartbeatRepository;
import com.perseus.backend.agent.domain.AgentStatus;
import com.perseus.backend.agent.infrastructure.AgentProperties;
import com.perseus.backend.event.domain.EventSeverity;
import com.perseus.backend.event.domain.EventType;
import com.perseus.backend.event.domain.ReportedAgentStatus;
import com.perseus.backend.event.domain.ReportedAgentStatusRepository;
import com.perseus.backend.vm.domain.VmRecordRepository;
import java.time.Clock;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

// Agent status is judged from heartbeat age, so nothing arrives when an agent goes quiet.
// This check runs periodically and records agent.status.changed when the judged status moves.
@Component
public class AgentStatusWatcher {

    private final AgentHeartbeatRepository heartbeatRepository;
    private final ReportedAgentStatusRepository reportedRepository;
    private final VmRecordRepository vmRecordRepository;
    private final AgentDataService agentDataService;
    private final AgentProperties agentProperties;
    private final EventRecorder eventRecorder;
    private final Clock clock;

    public AgentStatusWatcher(
            AgentHeartbeatRepository heartbeatRepository,
            ReportedAgentStatusRepository reportedRepository,
            VmRecordRepository vmRecordRepository,
            AgentDataService agentDataService,
            AgentProperties agentProperties,
            EventRecorder eventRecorder,
            Clock clock) {
        this.heartbeatRepository = heartbeatRepository;
        this.reportedRepository = reportedRepository;
        this.vmRecordRepository = vmRecordRepository;
        this.agentDataService = agentDataService;
        this.agentProperties = agentProperties;
        this.eventRecorder = eventRecorder;
        this.clock = clock;
    }

    @Scheduled(fixedDelayString = "${perseus.event.agent-status-check-interval-ms:5000}")
    public void check() {
        Instant now = clock.instant();
        for (AgentHeartbeat heartbeat : heartbeatRepository.findAll()) {
            // Agents of deleted or unknown VMs would only produce noise on the timeline.
            if (!vmRecordRepository.existsById(heartbeat.getVmId())) {
                continue;
            }
            AgentStatus current = agentDataService.statusOf(heartbeat, now);
            ReportedAgentStatus reported = reportedRepository.findById(heartbeat.getVmId()).orElse(null);
            AgentStatus previous = reported == null ? AgentStatus.UNKNOWN : reported.getStatus();
            if (current == previous) {
                continue;
            }
            Instant occurredAt = changedAt(heartbeat, current);
            if (reported == null) {
                reported = new ReportedAgentStatus(heartbeat.getVmId(), current, occurredAt);
            } else {
                reported.change(current, occurredAt);
            }
            reportedRepository.save(reported);

            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("from", previous.name());
            payload.put("to", current.name());
            payload.put("last_heartbeat_at", heartbeat.getReceivedAt().toString());
            EventSeverity severity = current == AgentStatus.ONLINE ? EventSeverity.INFO : EventSeverity.WARNING;
            eventRecorder.record(EventType.AGENT_STATUS_CHANGED, severity, heartbeat.getVmId(), occurredAt, payload);
        }
    }

    // The moment the threshold was crossed, not the moment this check noticed it.
    private Instant changedAt(AgentHeartbeat heartbeat, AgentStatus status) {
        return switch (status) {
            case STALE -> heartbeat.getReceivedAt().plus(agentProperties.staleAfter());
            case OFFLINE -> heartbeat.getReceivedAt().plus(agentProperties.offlineAfter());
            default -> heartbeat.getReceivedAt();
        };
    }
}
