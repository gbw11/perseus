package com.xenova.backend.agent.api;

import com.xenova.backend.agent.application.AgentExitCommand;
import com.xenova.backend.agent.application.AgentExitService;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/vms/{vmId}/faults")
public class AgentExitController {

    private final AgentExitService service;

    public AgentExitController(AgentExitService service) {
        this.service = service;
    }

    @PostMapping("/agent-exit")
    public ResponseEntity<AgentExitCommand> agentExit(@PathVariable String vmId) {
        return ResponseEntity.accepted().body(service.send(vmId));
    }
}
