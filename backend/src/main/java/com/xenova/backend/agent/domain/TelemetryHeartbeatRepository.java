package com.xenova.backend.agent.domain;

import org.springframework.data.jpa.repository.JpaRepository;

public interface TelemetryHeartbeatRepository extends JpaRepository<TelemetryHeartbeat, String> {
}
