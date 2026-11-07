package com.xenova.backend.agent.api;

import com.xenova.backend.agent.application.AgentStressCommand;
import com.xenova.backend.agent.application.AgentStressService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/vms/{vmId}/stress")
public class AgentStressController {

    private final AgentStressService service;

    public AgentStressController(AgentStressService service) {
        this.service = service;
    }

    @PostMapping("/cpu")
    public ResponseEntity<AgentStressCommand> cpu(
            @PathVariable String vmId, @Valid @RequestBody CpuStressRequest request) {
        return ResponseEntity.accepted()
                .body(service.cpu(vmId, request.workers(), request.loadPercent(), request.durationS()));
    }

    @PostMapping("/memory")
    public ResponseEntity<AgentStressCommand> memory(
            @PathVariable String vmId, @Valid @RequestBody MemoryStressRequest request) {
        return ResponseEntity.accepted().body(service.memory(vmId, request.memoryMib(), request.durationS()));
    }
}
