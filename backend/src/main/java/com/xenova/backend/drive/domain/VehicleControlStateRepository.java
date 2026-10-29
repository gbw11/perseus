package com.xenova.backend.drive.domain;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface VehicleControlStateRepository extends JpaRepository<VehicleControlState, Long> {

    Optional<VehicleControlState> findByScopeKey(String scopeKey);
}
