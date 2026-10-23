package com.xenova.backend.agent.domain;

import org.springframework.data.jpa.repository.JpaRepository;

public interface AgentHeartbeatRepository extends JpaRepository<AgentHeartbeat, String> {
}
