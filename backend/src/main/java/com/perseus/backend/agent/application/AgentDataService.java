package com.perseus.backend.agent.application;

import com.perseus.backend.agent.domain.AgentHeartbeat;
import com.perseus.backend.agent.domain.AgentHeartbeatRepository;
import com.perseus.backend.agent.domain.AgentInventory;
import com.perseus.backend.agent.domain.AgentInventoryRepository;
import com.perseus.backend.agent.domain.AgentStatus;
import com.perseus.backend.agent.domain.InventoryReport;
import com.perseus.backend.agent.infrastructure.AgentProperties;
import java.time.Duration;
import java.time.Instant;
import java.util.Optional;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

// Stores what the agents report. The MQTT receivers (MQTT-01/02) will call the record methods.
@Service
@EnableConfigurationProperties(AgentProperties.class)
public class AgentDataService {

    private final AgentInventoryRepository inventoryRepository;
    private final AgentHeartbeatRepository heartbeatRepository;
    private final AgentProperties properties;

    public AgentDataService(
            AgentInventoryRepository inventoryRepository,
            AgentHeartbeatRepository heartbeatRepository,
            AgentProperties properties) {
        this.inventoryRepository = inventoryRepository;
        this.heartbeatRepository = heartbeatRepository;
        this.properties = properties;
    }

    @Transactional
    public void recordInventory(String vmId, InventoryReport report, Instant receivedAt) {
        AgentInventory inventory = inventoryRepository.findById(vmId).orElseGet(() -> new AgentInventory(vmId));
        inventory.update(report, receivedAt);
        inventoryRepository.save(inventory);
    }

    @Transactional
    public void recordHeartbeat(String vmId, String bootId, String agentInstanceId, Instant sentAt, Instant receivedAt) {
        AgentHeartbeat heartbeat = heartbeatRepository.findById(vmId).orElseGet(() -> new AgentHeartbeat(vmId));
        heartbeat.update(bootId, agentInstanceId, sentAt, receivedAt);
        heartbeatRepository.save(heartbeat);
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
