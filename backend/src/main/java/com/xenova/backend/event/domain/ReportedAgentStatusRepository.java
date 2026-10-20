package com.xenova.backend.event.domain;

import org.springframework.data.jpa.repository.JpaRepository;

public interface ReportedAgentStatusRepository extends JpaRepository<ReportedAgentStatus, String> {
}
