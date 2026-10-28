package com.xenova.backend.event.domain;

import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.JpaSpecificationExecutor;

public interface VmEventRepository extends JpaRepository<VmEvent, Long>, JpaSpecificationExecutor<VmEvent> {

    boolean existsByEventId(String eventId);

    Optional<VmEvent> findByEventId(String eventId);
}
