package com.xenova.backend.vm.api;

import com.xenova.backend.vm.application.VmSynchronizationService;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/vms/synchronizations")
public class VmSynchronizationController {

    private final VmSynchronizationService service;

    public VmSynchronizationController(VmSynchronizationService service) {
        this.service = service;
    }

    @PostMapping
    public VmSynchronizationResponse synchronize() {
        return service.synchronize();
    }
}
