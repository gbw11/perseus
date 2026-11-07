package com.xenova.backend.agent.application;

import com.xenova.backend.agent.domain.AgentCommandResult;
import com.xenova.backend.agent.domain.AgentCommandResult.Observation;
import com.xenova.backend.agent.domain.AgentCommandResultReport;
import com.xenova.backend.agent.domain.AgentCommandResultRepository;
import com.xenova.backend.agent.domain.AgentCommandStatus;
import java.time.Instant;
import java.util.Optional;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class AgentCommandResultStore {

    private final AgentCommandResultRepository repository;

    public AgentCommandResultStore(AgentCommandResultRepository repository) {
        this.repository = repository;
    }

    @Transactional
    public Observation record(String vmId, AgentCommandResultReport report) {
        AgentCommandResult existing = repository.findByVmIdAndCommandId(vmId, report.commandId()).orElse(null);
        if (existing == null) {
            repository.save(new AgentCommandResult(vmId, report));
            return Observation.UPDATED;
        }
        return existing.observe(report);
    }

    @Transactional
    public void recordPending(
            String vmId, String commandId, String operationId, String bootId, Instant submittedAt) {
        repository.findByVmIdAndCommandId(vmId, commandId)
                .orElseGet(() -> repository.save(
                        new AgentCommandResult(vmId, commandId, operationId, bootId, submittedAt)));
    }

    @Transactional
    public void discardPending(String vmId, String commandId) {
        repository.findByVmIdAndCommandId(vmId, commandId)
                .filter(result -> result.getStatus() == AgentCommandStatus.PENDING)
                .ifPresent(result -> repository.deleteByVmIdAndCommandId(vmId, commandId));
    }

    @Transactional(readOnly = true)
    public Optional<AgentCommandResult> find(String vmId, String commandId) {
        return repository.findByVmIdAndCommandId(vmId, commandId);
    }
}
