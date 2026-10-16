package com.perseus.backend.report.performance.domain;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

public interface PerformanceTrialRepository
        extends JpaRepository<PerformanceTrial, String>, JpaSpecificationExecutor<PerformanceTrial> {
}
