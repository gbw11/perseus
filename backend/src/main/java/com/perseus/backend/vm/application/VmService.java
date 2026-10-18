package com.perseus.backend.vm.application;

import com.perseus.backend.common.error.ApiException;
import com.perseus.backend.template.application.TemplateCatalog;
import com.perseus.backend.template.domain.VmTemplate;
import com.perseus.backend.vm.domain.CreateVmCommand;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmControlPort;
import com.perseus.backend.vm.domain.VmRecord;
import com.perseus.backend.vm.domain.VmRecordRepository;
import com.perseus.backend.vm.domain.VmRuntime;
import java.time.Clock;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Function;
import java.util.stream.Collectors;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

// Spring owns VM records (name, template); dom0-manager only reports domains that exist in Xen
// (vm-api-contract-v1 1.2). A recorded VM without a domain is reported as stopped.
@Service
public class VmService {

    // dom0-manager rejected the request itself, so no domain or disk was created for this vm_id.
    private static final Set<String> REJECTED_BY_DOM0 = Set.of("INVALID_ARGUMENT", "INSUFFICIENT_RESOURCES");
    private static final String DOMAIN_EXISTS = "VM_ALREADY_EXISTS";

    private final VmControlPort vmControlPort;
    private final VmRecordRepository vmRecordRepository;
    private final TemplateCatalog templateCatalog;
    private final Clock clock;

    public VmService(
            VmControlPort vmControlPort,
            VmRecordRepository vmRecordRepository,
            TemplateCatalog templateCatalog,
            Clock clock) {
        this.vmControlPort = vmControlPort;
        this.vmRecordRepository = vmRecordRepository;
        this.templateCatalog = templateCatalog;
        this.clock = clock;
    }

    // Domains unknown to Spring (e.g. started by a platform tool) are left out.
    public List<Vm> list() {
        Map<String, VmRuntime> runtimes = vmControlPort.list().stream()
                .collect(Collectors.toMap(VmRuntime::vmId, Function.identity(), (first, second) -> first));
        return vmRecordRepository.findAllByOrderByCreatedAtAsc().stream()
                .map(record -> Vm.of(record, runtimes.get(record.getVmId())))
                .toList();
    }

    public Vm get(String vmId) {
        VmRecord record = findRecord(vmId);
        return Vm.of(record, vmControlPort.find(vmId).orElse(null));
    }

    // A retried request with the same name and spec returns the existing VM (200) instead of a duplicate.
    public CreateResult create(CreateVmCommand command) {
        VmTemplate template = templateCatalog.find(command.templateId(), command.templateVersion())
                .orElseThrow(() -> new ApiException(
                        HttpStatus.NOT_FOUND, "TEMPLATE_NOT_FOUND", "존재하지 않는 템플릿입니다."));
        if (command.memoryMib() < template.minMemoryMib()) {
            throw new ApiException(
                    HttpStatus.UNPROCESSABLE_ENTITY,
                    "VALIDATION_ERROR",
                    "memory_mib는 이 템플릿의 최소값(" + template.minMemoryMib() + ") 이상이어야 합니다.");
        }

        VmRecord existing = vmRecordRepository.findByName(command.name()).orElse(null);
        if (existing != null) {
            if (!existing.hasSameSpec(command)) {
                throw new ApiException(HttpStatus.CONFLICT, "VM_NAME_CONFLICT", "이미 사용 중인 VM 이름입니다.");
            }
            return new CreateResult(ensureRunning(existing), false);
        }

        VmRecord record = vmRecordRepository.save(new VmRecord(UUID.randomUUID().toString(), command, clock.instant()));
        try {
            return new CreateResult(Vm.of(record, vmControlPort.launch(record.getVmId(), record.launchSpec())), true);
        } catch (ApiException exception) {
            if (DOMAIN_EXISTS.equals(exception.getCode())) {
                return new CreateResult(get(record.getVmId()), true);
            }
            // On a timeout the domain may still be coming up, so the record is kept and a retry can recover it.
            if (REJECTED_BY_DOM0.contains(exception.getCode())) {
                vmRecordRepository.delete(record);
            }
            throw exception;
        }
    }

    // There is no start endpoint: the same vm_id is launched again and its preserved disk is reused.
    public Vm start(String vmId) {
        return ensureRunning(findRecord(vmId));
    }

    public Vm stop(String vmId) {
        VmRecord record = findRecord(vmId);
        vmControlPort.stop(vmId);
        return Vm.of(record, null);
    }

    // dom0-manager deletes the disk as well, so the VM cannot be started again and its record is removed.
    public void destroy(String vmId) {
        vmControlPort.destroy(vmId);
        vmRecordRepository.deleteById(vmId);
    }

    private Vm ensureRunning(VmRecord record) {
        VmRuntime runtime = vmControlPort.find(record.getVmId()).orElse(null);
        if (runtime != null) {
            return Vm.of(record, runtime);
        }
        try {
            return Vm.of(record, vmControlPort.launch(record.getVmId(), record.launchSpec()));
        } catch (ApiException exception) {
            // 409 VMExists for our own vm_id means an earlier launch already succeeded (contract 10.1).
            if (DOMAIN_EXISTS.equals(exception.getCode())) {
                return Vm.of(record, vmControlPort.find(record.getVmId()).orElse(null));
            }
            throw exception;
        }
    }

    private VmRecord findRecord(String vmId) {
        return vmRecordRepository.findById(vmId)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "VM_NOT_FOUND", "존재하지 않는 VM입니다."));
    }

    public record CreateResult(Vm vm, boolean created) {
    }
}
