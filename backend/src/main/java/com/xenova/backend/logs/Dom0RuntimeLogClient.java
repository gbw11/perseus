package com.xenova.backend.logs;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.vm.infrastructure.Dom0ManagerClient;
import com.xenova.backend.vm.infrastructure.Dom0ManagerProperties;
import java.util.Map;
import org.springframework.stereotype.Component;

// Separate from VmControlPort so VM integration tests can replace the control client without breaking log beans.
@Component
public class Dom0RuntimeLogClient implements RuntimeLogPort {

    private final Dom0ManagerClient client;

    public Dom0RuntimeLogClient(Dom0ManagerProperties properties, ObjectMapper objectMapper) {
        this.client = new Dom0ManagerClient(properties, objectMapper);
    }

    @Override
    public JsonNode read(Map<String, Object> filters, boolean sources) {
        return client.runtimeLogs(filters, sources);
    }
}
