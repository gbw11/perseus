package com.xenova.backend.agent.domain;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface AgentCommandResultRepository extends JpaRepository<AgentCommandResult, Long> {

    Optional<AgentCommandResult> findByVmIdAndCommandId(String vmId, String commandId);

    void deleteByVmIdAndCommandId(String vmId, String commandId);
}
