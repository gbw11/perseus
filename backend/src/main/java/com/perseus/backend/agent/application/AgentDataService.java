package com.perseus.backend.agent.application;

import com.perseus.backend.agent.domain.AgentHeartbeat;
import com.perseus.backend.agent.domain.AgentHeartbeatRepository;
import com.perseus.backend.agent.domain.AgentInventory;
import com.perseus.backend.agent.domain.AgentInventoryRepository;
import com.perseus.backend.agent.domain.AgentStatus;
import com.perseus.backend.agent.domain.InventoryReport;
import com.perseus.backend.agent.domain.MetricSample;
import com.perseus.backend.agent.domain.MetricSampleRepository;
import com.perseus.backend.agent.domain.ProcessInfo;
import com.perseus.backend.agent.domain.ProcessSnapshot;
import com.perseus.backend.agent.domain.ProcessSnapshotRepository;
import com.perseus.backend.agent.infrastructure.AgentProperties;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

// Stores what the agents report over MQTT (heartbeat, inventory, metrics).
@Service
@EnableConfigurationProperties(AgentProperties.class)
public class AgentDataService {

    private final AgentInventoryRepository inventoryRepository;
    private final AgentHeartbeatRepository heartbeatRepository;
    private final MetricSampleRepository metricSampleRepository;
    private final ProcessSnapshotRepository processSnapshotRepository;
    private final AgentProperties properties;

    public AgentDataService(
            AgentInventoryRepository inventoryRepository,
            AgentHeartbeatRepository heartbeatRepository,
            MetricSampleRepository metricSampleRepository,
            ProcessSnapshotRepository processSnapshotRepository,
            AgentProperties properties) {
        this.inventoryRepository = inventoryRepository;
        this.heartbeatRepository = heartbeatRepository;
        this.metricSampleRepository = metricSampleRepository;
        this.processSnapshotRepository = processSnapshotRepository;
        this.properties = properties;
    }

    @Transactional
    public boolean recordInventory(String vmId, InventoryReport report, Instant receivedAt) {
        AgentInventory inventory = inventoryRepository.findById(vmId).orElseGet(() -> new AgentInventory(vmId));
        if (!inventory.isSupersededBy(report.observedAt())) {
            return false;
        }
        inventory.update(report, receivedAt);
        inventoryRepository.save(inventory);
        return true;
    }

    @Transactional
    public boolean recordHeartbeat(
            String vmId, String bootId, String agentInstanceId, long seq, Instant sentAt, Instant receivedAt) {
        AgentHeartbeat heartbeat = heartbeatRepository.findById(vmId).orElseGet(() -> new AgentHeartbeat(vmId));
        if (!heartbeat.isSupersededBy(agentInstanceId, seq, sentAt)) {
            return false;
        }
        heartbeat.update(bootId, agentInstanceId, seq, sentAt, receivedAt);
        heartbeatRepository.save(heartbeat);
        return true;
    }

    // Redelivered MQTT messages (QoS 1) keep their message_id, so a known sample_id is skipped.
    @Transactional
    public boolean recordMetrics(MetricSample sample, List<ProcessInfo> processes) {
        if (metricSampleRepository.existsById(sample.getSampleId())) {
            return false;
        }
        metricSampleRepository.save(sample);
        ProcessSnapshot snapshot = processSnapshotRepository.findById(sample.getVmId())
                .orElseGet(() -> new ProcessSnapshot(sample.getVmId()));
        if (snapshot.getObservedAt() == null || !sample.getObservedAt().isBefore(snapshot.getObservedAt())) {
            snapshot.update(processes, sample.getObservedAt(), sample.getReceivedAt());
            processSnapshotRepository.save(snapshot);
        }
        return true;
    }

    @Transactional(readOnly = true)
    public Optional<AgentInventory> findInventory(String vmId) {
        return inventoryRepository.findById(vmId);
    }

    @Transactional(readOnly = true)
    public Optional<AgentHeartbeat> findHeartbeat(String vmId) {
        return heartbeatRepository.findById(vmId);
    }

    public AgentStatus statusOf(AgentHeartbeat heartbeat, Instant now) {
        if (heartbeat == null) {
            return AgentStatus.UNKNOWN;
        }
        Duration age = Duration.between(heartbeat.getReceivedAt(), now);
        if (age.compareTo(properties.staleAfter()) <= 0) {
            return AgentStatus.ONLINE;
        }
        if (age.compareTo(properties.offlineAfter()) <= 0) {
            return AgentStatus.STALE;
        }
        return AgentStatus.OFFLINE;
    }
}
