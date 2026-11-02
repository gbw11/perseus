package com.xenova.backend.vm.api;

import com.xenova.backend.vm.application.VmRegistrationService;
import java.net.URI;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class VmRegistrationController {

    private final VmRegistrationService service;

    public VmRegistrationController(VmRegistrationService service) {
        this.service = service;
    }

    @PostMapping("/api/v1/vms/registrations")
    public ResponseEntity<VmRegistrationResponse> register(
            @RequestBody VmRegistrationRequest request) {
        VmRegistrationService.Result result = service.register(request);
        VmRegistrationResponse response = VmRegistrationResponse.confirmed(result.vmId());
        return result.created()
                ? ResponseEntity.created(URI.create("/api/v1/vms/" + result.vmId())).body(response)
                : ResponseEntity.ok(response);
    }
}

