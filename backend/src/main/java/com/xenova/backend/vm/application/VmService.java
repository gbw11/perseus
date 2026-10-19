package com.xenova.backend.vm.application;

import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.event.application.EventRecorder;
import com.xenova.backend.event.application.VmStateTracker;
import com.xenova.backend.template.application.TemplateCatalog;
import com.xenova.backend.template.domain.VmTemplate;
import com.xenova.backend.vm.domain.CreateVmCommand;
import com.xenova.backend.vm.domain.Vm;
import com.xenova.backend.vm.domain.VmControlPort;
import com.xenova.backend.vm.domain.VmLifecycle;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import com.xenova.backend.vm.domain.VmRuntime;
import com.xenova.backend.vm.domain.VmState;
import java.time.Clock;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Function;
import java.util.function.Supplier;
import java.util.stream.Collectors;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

// Spring owns the public VM specification and issues the permanent vm_id. The platform owns the registry,
// disk lifecycle, and Xen runtime. A registered VM without a Xen domain is reported as stopped.
@Service
public class VmService {

    // dom0-manager rejected the request itself, so no domain or disk was created for this vm_id.
    // NOT_SUPPORTED: until the platform accepts a caller-chosen vm_id, every new create is refused this way.
    private static final Set<String> REJECTED_BY_DOM0 =
            Set.of("INVALID_ARGUMENT", "INSUFFICIENT_RESOURCES", "VM_CONTROL_NOT_SUPPORTED");
    private static final String DOMAIN_EXISTS = "VM_ALREADY_EXISTS";

    private final VmControlPort vmControlPort;
    private final VmRecordRepository vmRecordRepository;
    private final TemplateCatalog templateCatalog;
    private final EventRecorder eventRecorder;
    private final VmStateTracker stateTracker;
    private final Clock clock;

    public VmService(
            VmControlPort vmControlPort,
            VmRecordRepository vmRecordRepository,
            TemplateCatalog templateCatalog,
            EventRecorder eventRecorder,
            VmStateTracker stateTracker,
            Clock clock) {
        this.vmControlPort = vmControlPort;
        this.vmRecordRepository = vmRecordRepository;
        this.templateCatalog = templateCatalog;
        this.eventRecorder = eventRecorder;
        this.stateTracker = stateTracker;
        this.clock = clock;
    }

    // Domains unknown to Spring (e.g. started by a platform tool) are left out.
    public List<Vm> list() {
        Map<String, VmRuntime> runtimes = vmControlPort.list().stream()
                .collect(Collectors.toMap(VmRuntime::vmId, Function.identity(), (first, second) -> first));
        return vmRecordRepository.findAllByLifecycleOrLifecycleIsNullOrderByCreatedAtAsc(VmLifecycle.REGISTERED).stream()
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
            if (!existing.isRegistered()) {
                throw new ApiException(HttpStatus.CONFLICT, "VM_NAME_CONFLICT", "보관 중인 VM이 사용하는 이름입니다.");
            }
            if (!existing.hasSameSpec(command)) {
                throw new ApiException(HttpStatus.CONFLICT, "VM_NAME_CONFLICT", "이미 사용 중인 VM 이름입니다.");
            }
            return new CreateResult(underOperation(existing.getVmId(), () -> ensureRunning(existing)), false);
        }

        VmRecord record = vmRecordRepository.save(
                new VmRecord(UUID.randomUUID().toString(), command, template.guestKind(), clock.instant()));
        return new CreateResult(underOperation(record.getVmId(), () -> launchNew(record)), true);
    }

    private Vm launchNew(VmRecord record) {
        Vm vm;
        try {
            vm = Vm.of(record, vmControlPort.launch(record.getVmId(), record.launchSpec()));
        } catch (ApiException exception) {
            if (!DOMAIN_EXISTS.equals(exception.getCode())) {
                // On a timeout the domain may still be coming up, so the record is kept and a retry can recover it.
                if (REJECTED_BY_DOM0.contains(exception.getCode())) {
                    vmRecordRepository.delete(record);
                }
                throw exception;
            }
            vm = get(record.getVmId());
        }
        eventRecorder.vmCreated(record);
        // vm.created already tells the story; the state is only remembered so polling does not repeat it.
        stateTracker.remember(record.getVmId(), vm.state());
        return vm;
    }

    // There is no start endpoint: the same vm_id is launched again and its preserved disk is reused.
    // A domain that is still in Xen but not running is refused, as the platform CLI does: there is no automatic
    // recovery, so the user stops it first and starts again.
    public Vm start(String vmId) {
        VmRecord record = findRecord(vmId);
        return underOperation(vmId, () -> {
            VmRuntime runtime = vmControlPort.find(vmId).orElse(null);
            if (runtime == null) {
                return launch(record);
            }
            rejectUnstartable(runtime.state());
            return Vm.of(record, runtime);
        });
    }

    // The state is read first so the event says what was stopped; a VM that is already off records nothing.
    public Vm stop(String vmId) {
        VmRecord record = findRecord(vmId);
        return underOperation(vmId, () -> {
            VmState before = vmControlPort.find(vmId).map(VmRuntime::state).orElse(null);
            if (before == null) {
                return Vm.of(record, null);
            }
            vmControlPort.stop(vmId);
            stateTracker.changedByUser(vmId, before, VmState.STOPPED, VmStateTracker.CAUSE_USER_STOP);
            return Vm.of(record, null);
        });
    }

    // The platform retires the disk instead of deleting it. Keep the specification and vm_id for a future restore,
    // but hide the retired VM from the current v1 list/get/start/stop surface.
    public void destroy(String vmId) {
        VmRecord record = findRecord(vmId);
        underOperation(vmId, () -> {
            vmControlPort.destroy(vmId);
            record.retire();
            vmRecordRepository.save(record);
            stateTracker.forget(vmId);
            return null;
        });
        eventRecorder.vmDeleted(vmId, record.getName());
    }

    // Marks the VM busy so the periodic state check does not report this operation's
    // intermediate states as detected changes.
    private <T> T underOperation(String vmId, Supplier<T> operation) {
        stateTracker.beginOperation(vmId);
        try {
            return operation.get();
        } finally {
            stateTracker.endOperation(vmId);
        }
    }

    private Vm ensureRunning(VmRecord record) {
        VmRuntime runtime = vmControlPort.find(record.getVmId()).orElse(null);
        if (runtime != null) {
            return Vm.of(record, runtime);
        }
        return launch(record);
    }

    private static void rejectUnstartable(VmState state) {
        String message = switch (state) {
            case RUNNING -> null;
            case CRASHED -> "VM이 크래시 상태입니다. 정지한 뒤 다시 시작하세요.";
            case PAUSED -> "VM이 일시정지 상태입니다. 정지한 뒤 다시 시작하세요.";
            case STOPPING -> "VM이 종료되는 중입니다. 종료된 뒤 다시 시작하세요.";
            case STOPPED -> null;
        };
        if (message != null) {
            throw new ApiException(HttpStatus.CONFLICT, "INVALID_VM_STATE", message);
        }
    }

    private Vm launch(VmRecord record) {
        Vm vm;
        try {
            vm = Vm.of(record, vmControlPort.launch(record.getVmId(), record.launchSpec()));
        } catch (ApiException exception) {
            // 409 VMExists for our own vm_id means an earlier launch already succeeded (contract 10.1).
            if (!DOMAIN_EXISTS.equals(exception.getCode())) {
                throw exception;
            }
            vm = Vm.of(record, vmControlPort.find(record.getVmId()).orElse(null));
        }
        stateTracker.changedByUser(record.getVmId(), VmState.STOPPED, vm.state(), VmStateTracker.CAUSE_USER_START);
        return vm;
    }

    private VmRecord findRecord(String vmId) {
        return vmRecordRepository.findById(vmId)
                .filter(VmRecord::isRegistered)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "VM_NOT_FOUND", "존재하지 않는 VM입니다."));
    }

    public record CreateResult(Vm vm, boolean created) {
    }
}
