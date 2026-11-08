package com.xenova.backend.agent.api;

import com.xenova.backend.agent.application.AutoDelayCommand;
import com.xenova.backend.agent.application.AutoDelayService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/vms/{vmId}/faults")
public class AutoDelayController {

    private final AutoDelayService service;

    public AutoDelayController(AutoDelayService service) {
        this.service = service;
    }

    @PostMapping("/auto-delay")
    public ResponseEntity<AutoDelayCommand> autoDelay(
            @PathVariable String vmId, @Valid @RequestBody AutoDelayRequest request) {
        return ResponseEntity.accepted().body(service.send(vmId, request.delayMs(), request.durationS()));
    }
}
