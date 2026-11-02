package com.xenova.backend.vm.infrastructure;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.vm.domain.VmRegistrationInventory;
import com.xenova.backend.vm.domain.VmRegistrationInventoryPort;
import java.util.List;
import org.springframework.stereotype.Component;

// Kept as a separate Spring bean from VmControlPort. Integration tests replace VmControlPort with a mock, and that
// must not also replace the platform-registry dependency required by VmSynchronizationService.
@Component
public class Dom0VmRegistrationInventoryClient implements VmRegistrationInventoryPort {

    private final Dom0ManagerClient client;

    public Dom0VmRegistrationInventoryClient(Dom0ManagerProperties properties, ObjectMapper objectMapper) {
        this.client = new Dom0ManagerClient(properties, objectMapper);
    }

    @Override
    public List<VmRegistrationInventory> listRegistrations() {
        return client.listRegistrations();
    }
}
