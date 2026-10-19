package com.perseus.backend.event.domain;

import org.springframework.data.jpa.repository.JpaRepository;

public interface ReportedVmStateRepository extends JpaRepository<ReportedVmState, String> {
}
