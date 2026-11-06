package com.xenova.backend.vm.api;

import com.xenova.backend.agent.application.AgentDataService;
import com.xenova.backend.agent.domain.AgentInventory;
import com.xenova.backend.agent.domain.AgentStatus;
import com.xenova.backend.vm.application.VmService;
import com.xenova.backend.vm.application.VmService.CreateResult;
import com.xenova.backend.vm.domain.Vm;
import com.xenova.backend.vm.domain.VmRole;
import jakarta.validation.Valid;
import java.net.URI;
import java.time.Clock;
import java.time.Instant;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
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
        VmService.VmList list = vmService.listObserved();
        return new VmListResponse(list.vms().stream().map(vm -> withAgentStatus(vm, now)).toList(), list.observedAt());
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

    @PatchMapping("/{vmId}")
    public VmResponse update(@PathVariable String vmId, @Valid @RequestBody UpdateVmRequest request) {
        return withAgentStatus(vmService.updateRole(vmId, VmRole.fromValue(request.role())), clock.instant());
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
        AgentStatus agentStatus = agentDataService.statusOf(agentDataService.findHeartbeat(vm.vmId()).orElse(null), now);
        String ipv4 = agentDataService.findInventory(vm.vmId()).flatMap(AgentInventory::primaryIpv4).orElse(null);
        return VmResponse.of(vm, agentStatus, ipv4);
    }
}
