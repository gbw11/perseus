package com.xenova.backend.agent.application;

import com.xenova.backend.agent.domain.AgentInventoryRepository;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

// Contract mqtt-contract-v1 §3: when a VM is deleted the backend removes that VM's retained inventory. The stored
// inventory row goes with it so the deleted VM's last OS report is not shown as if it were current.
// Called after dom0-manager has confirmed the delete; nothing here may fail the delete itself.
@Service
public class AgentInventoryCleaner {

    private static final Logger log = LoggerFactory.getLogger(AgentInventoryCleaner.class);

    private final AgentInventoryRepository inventoryRepository;
    private final Supplier<RetainedInventoryClearer> clearer;

    @Autowired
    public AgentInventoryCleaner(
            AgentInventoryRepository inventoryRepository, ObjectProvider<RetainedInventoryClearer> clearer) {
        this(inventoryRepository, (Supplier<RetainedInventoryClearer>) clearer::getIfAvailable);
    }

    AgentInventoryCleaner(AgentInventoryRepository inventoryRepository, Supplier<RetainedInventoryClearer> clearer) {
        this.inventoryRepository = inventoryRepository;
        this.clearer = clearer;
    }

    public void vmDeleted(String vmId) {
        RetainedInventoryClearer target = clearer.get();
        if (target == null) {
            log.warn("MQTT is disabled: retained inventory of deleted VM {} was not cleared", vmId);
        } else {
            target.clearRetainedInventory(vmId);
        }
        try {
            if (inventoryRepository.existsById(vmId)) {
                inventoryRepository.deleteById(vmId);
            }
        } catch (RuntimeException exception) {
            log.warn("Could not delete the stored inventory of deleted VM {}: {}", vmId, exception.getMessage());
        }
    }
}
