package com.xenova.backend.agent.application;

import com.xenova.backend.agent.domain.AgentCommandResult;
import com.xenova.backend.agent.domain.AgentCommandResult.Observation;
import com.xenova.backend.agent.domain.AgentCommandResultReport;
import com.xenova.backend.agent.domain.AgentCommandResultRepository;
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
}
