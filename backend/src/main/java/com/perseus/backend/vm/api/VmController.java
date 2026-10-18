package com.perseus.backend.vm.api;

import com.perseus.backend.agent.application.AgentDataService;
import com.perseus.backend.vm.application.VmService;
import com.perseus.backend.vm.application.VmService.CreateResult;
import com.perseus.backend.vm.domain.Vm;
import jakarta.validation.Valid;
import java.net.URI;
import java.time.Clock;
import java.time.Instant;
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
    private final AgentDataService agentDataService;
    private final Clock clock;

    public VmController(VmService vmService, AgentDataService agentDataService, Clock clock) {
        this.vmService = vmService;
        this.agentDataService = agentDataService;
        this.clock = clock;
    }

    @GetMapping
    public VmListResponse list() {
        Instant now = clock.instant();
        return new VmListResponse(vmService.list().stream().map(vm -> withAgentStatus(vm, now)).toList(), now);
    }

    // 201 for a new VM, 200 when the same name and spec were already created (retry).
    @PostMapping
    public ResponseEntity<Vm> create(@Valid @RequestBody CreateVmRequest request) {
        CreateResult result = vmService.create(request.toCommand());
        if (!result.created()) {
            return ResponseEntity.ok(result.vm());
        }
        return ResponseEntity.created(URI.create("/api/v1/vms/" + result.vm().vmId())).body(result.vm());
    }

    @GetMapping("/{vmId}")
    public VmResponse get(@PathVariable String vmId) {
        return withAgentStatus(vmService.get(vmId), clock.instant());
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

    private VmResponse withAgentStatus(Vm vm, Instant now) {
        return VmResponse.of(vm, agentDataService.statusOf(agentDataService.findHeartbeat(vm.vmId()).orElse(null), now));
    }
}
