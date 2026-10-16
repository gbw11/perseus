package com.perseus.backend.agent.domain;

import org.springframework.data.jpa.repository.JpaRepository;

public interface MetricSampleRepository extends JpaRepository<MetricSample, String> {
}
