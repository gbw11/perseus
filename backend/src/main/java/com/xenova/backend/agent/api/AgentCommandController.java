package com.xenova.backend.agent.api;

import com.xenova.backend.agent.application.AgentCommandQueryService;
import com.xenova.backend.agent.application.AgentCommandStatusResponse;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/vms/{vmId}/commands")
public class AgentCommandController {

    private final AgentCommandQueryService service;

    public AgentCommandController(AgentCommandQueryService service) {
        this.service = service;
    }

    @GetMapping("/{commandId}")
    public AgentCommandStatusResponse get(
            @PathVariable String vmId, @PathVariable String commandId) {
        return service.get(vmId, commandId);
    }
}
