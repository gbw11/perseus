package com.perseus.backend.agent.domain;

import java.time.Instant;
import java.util.List;
import org.springframework.data.jpa.repository.JpaRepository;

public interface MetricSampleRepository extends JpaRepository<MetricSample, String> {

    List<MetricSample> findByVmIdAndObservedAtGreaterThanEqualAndObservedAtLessThanOrderByObservedAtAsc(
            String vmId, Instant from, Instant to);
}
