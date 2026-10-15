package com.perseus.backend.vm.api;

import com.perseus.backend.vm.application.VmService;
import com.perseus.backend.vm.domain.Vm;
import jakarta.validation.Valid;
import java.net.URI;
import java.util.List;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/vms")
public class VmController {

    private final VmService vmService;

    public VmController(VmService vmService) {
        this.vmService = vmService;
    }

    @GetMapping
    public List<Vm> list() {
        return vmService.list();
    }

    @PostMapping
    public ResponseEntity<Vm> create(@Valid @RequestBody CreateVmRequest request) {
        Vm vm = vmService.create(request.toCommand());
        return ResponseEntity.created(URI.create("/api/v1/vms/" + vm.vmId())).body(vm);
    }

    @GetMapping("/{vmId}")
    public Vm get(@PathVariable String vmId) {
        return vmService.get(vmId);
    }

    @PostMapping("/{vmId}/start")
    public Vm start(@PathVariable String vmId) {
        return vmService.start(vmId);
    }

    @PostMapping("/{vmId}/stop")
    public Vm stop(@PathVariable String vmId) {
        return vmService.stop(vmId);
    }

    @DeleteMapping("/{vmId}")
    public ResponseEntity<Void> destroy(@PathVariable String vmId) {
        vmService.destroy(vmId);
        return ResponseEntity.noContent().build();
    }
}
