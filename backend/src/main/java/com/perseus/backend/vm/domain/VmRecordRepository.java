package com.perseus.backend.vm.domain;

import java.util.List;
import java.util.Optional;
import org.springframework.data.jpa.repository.JpaRepository;

public interface VmRecordRepository extends JpaRepository<VmRecord, String> {

    Optional<VmRecord> findByName(String name);

    List<VmRecord> findAllByLifecycleOrLifecycleIsNullOrderByCreatedAtAsc(VmLifecycle lifecycle);
}
