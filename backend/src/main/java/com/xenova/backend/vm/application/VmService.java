package com.xenova.backend.vm.application;

import com.xenova.backend.agent.application.AgentInventoryCleaner;
import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.event.application.EventRecorder;
import com.xenova.backend.event.application.VmStateTracker;
import com.xenova.backend.template.application.TemplateCatalog;
import com.xenova.backend.template.domain.VmTemplate;
import com.xenova.backend.vm.domain.CreateVmCommand;
import com.xenova.backend.vm.domain.Vm;
import com.xenova.backend.vm.domain.VmControlPort;
import com.xenova.backend.vm.domain.VmControlOperation;
import com.xenova.backend.vm.domain.VmLifecycle;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import com.xenova.backend.vm.domain.VmRole;
import com.xenova.backend.vm.domain.VmRuntime;
import com.xenova.backend.vm.domain.VmState;
import java.time.Clock;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

// Spring owns the public VM specification and issues the permanent vm_id. The platform owns the registry,
// disk lifecycle, and Xen runtime. A registered VM without a Xen domain is reported as stopped.
@Service
public class VmService {

    // dom0-manager rejected the request itself, so no domain or disk was created for this vm_id.
    // NOT_SUPPORTED: until the platform accepts a caller-chosen vm_id, every new create is refused this way.
    private static final Set<String> REJECTED_BY_DOM0 =
            Set.of("INVALID_ARGUMENT", "INSUFFICIENT_RESOURCES", "VM_CONTROL_NOT_SUPPORTED", "VM_NAME_CONFLICT");
    private static final String DOMAIN_EXISTS = "VM_ALREADY_EXISTS";

    private final VmControlPort vmControlPort;
    private final VmRecordRepository vmRecordRepository;
    private final TemplateCatalog templateCatalog;
    private final EventRecorder eventRecorder;
    private final VmStateTracker stateTracker;
    private final VmRuntimeCache runtimeCache;
    private final AgentInventoryCleaner inventoryCleaner;
    private final Clock clock;

    public VmService(
            VmControlPort vmControlPort,
            VmRecordRepository vmRecordRepository,
            TemplateCatalog templateCatalog,
            EventRecorder eventRecorder,
            VmStateTracker stateTracker,
            VmRuntimeCache runtimeCache,
            AgentInventoryCleaner inventoryCleaner,
            Clock clock) {
        this.vmControlPort = vmControlPort;
        this.vmRecordRepository = vmRecordRepository;
        this.templateCatalog = templateCatalog;
        this.eventRecorder = eventRecorder;
        this.stateTracker = stateTracker;
        this.runtimeCache = runtimeCache;
        this.inventoryCleaner = inventoryCleaner;
        this.clock = clock;
    }

    public record VmList(List<Vm> vms, Instant observedAt) {
    }

    // Domains unknown to Spring (e.g. started by a platform tool) are left out.
    public List<Vm> list() {
        return listObserved().vms();
    }

    // observedAt: when dom0-manager reported the runtimes, which can be a little before the response.
    public VmList listObserved() {
        return combine(runtimeCache.read());
    }

    // Always asks dom0-manager; the state check uses it so the cache is refreshed by one caller.
    public List<Vm> listLive() {
        return combine(runtimeCache.refresh()).vms();
    }

    public Vm get(String vmId) {
        VmRecord record = findRecord(vmId);
        return Vm.of(record, runtimeCache.find(vmId).orElse(null));
    }

    private VmList combine(VmRuntimeCache.Snapshot snapshot) {
        Map<String, VmRuntime> runtimes = snapshot.runtimes();
        List<Vm> vms = vmRecordRepository.findAllByLifecycleOrLifecycleIsNullOrderByCreatedAtAsc(VmLifecycle.REGISTERED)
                .stream()
                .map(record -> Vm.of(record, runtimes.get(record.getVmId())))
                .toList();
        return new VmList(vms, snapshot.observedAt());
    }

    // The role is a console label only: no dom0-manager call, no event, and the VM keeps running as it is.
    public Vm updateRole(String vmId, VmRole role) {
        VmRecord record = findRecord(vmId);
        record.changeRole(role);
        vmRecordRepository.save(record);
        // Same source as get(): a live dom0-manager call here could fail after the role was already saved.
        return Vm.of(record, runtimeCache.find(vmId).orElse(null));
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

        VmRecord record;
        try {
            record = vmRecordRepository.save(
                    new VmRecord(UUID.randomUUID().toString(), command, template.guestKind(), clock.instant()));
        } catch (DataIntegrityViolationException exception) {
            // Another request took the name between the check above and this insert (uk_vms_name).
            throw new ApiException(HttpStatus.CONFLICT, "VM_NAME_CONFLICT", "이미 사용 중인 VM 이름입니다.");
        }
        return new CreateResult(underOperation(record.getVmId(), () -> launchNew(record)), true);
    }

    private Vm launchNew(VmRecord record) {
        Vm vm;
        try {
            vm = launchDomain(record);
        } catch (ApiException exception) {
            // On a timeout the domain may still be coming up, so the record is kept and a retry can recover it.
            if (REJECTED_BY_DOM0.contains(exception.getCode())) {
                vmRecordRepository.delete(record);
            }
            throw exception;
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

    // The state is read first so the event says what was stopped. Always forward stop even without a runtime:
    // dom0-manager persists desired_state=stopped, which prevents recovery from relaunching the VM.
    public Vm stop(String vmId) {
        VmRecord record = findRecord(vmId);
        return underOperation(vmId, () -> {
            VmState before = vmControlPort.find(vmId).map(VmRuntime::state).orElse(null);
            vmControlPort.stop(vmId);
            runtimeCache.remove(vmId);
            if (before != null) {
                stateTracker.changedByUser(vmId, before, VmState.STOPPED, VmStateTracker.CAUSE_USER_STOP);
            }
            return Vm.of(record, null);
        });
    }

    public VmControlOperation reboot(String vmId, String requestKey) {
        String validatedKey = validateRequestKey(requestKey);
        findRecord(vmId);
        return vmControlPort.requestReboot(vmId, validatedKey);
    }

    public VmControlOperation controlOperation(String vmId, String requestKey) {
        String validatedKey = validateRequestKey(requestKey);
        findRecord(vmId);
        return vmControlPort.findControlOperation(vmId, validatedKey);
    }

    // The platform retires the disk instead of deleting it. Keep the specification and vm_id for a future restore,
    // but hide the retired VM from the current v1 list/get/start/stop surface.
    public void destroy(String vmId) {
        VmRecord record = findRecord(vmId);
        underOperation(vmId, () -> {
            vmControlPort.destroy(vmId);
            runtimeCache.remove(vmId);
            record.retire();
            vmRecordRepository.save(record);
            stateTracker.forget(vmId);
            return null;
        });
        eventRecorder.vmDeleted(vmId, record.getName());
        // Only after dom0-manager confirmed the delete. Failures are logged there and never fail the delete.
        inventoryCleaner.vmDeleted(vmId);
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
        Vm vm = launchDomain(record);
        stateTracker.changedByUser(record.getVmId(), VmState.STOPPED, vm.state(), VmStateTracker.CAUSE_USER_START);
        return vm;
    }

    // 409 VMExists for our own vm_id means an earlier launch already succeeded (contract 10.1), so the live
    // domain is read back. Both create and start use this: the cached list may not have the domain yet.
    private Vm launchDomain(VmRecord record) {
        VmRuntime runtime;
        try {
            runtime = vmControlPort.launch(record.getVmId(), record.launchSpec());
        } catch (ApiException exception) {
            if (!DOMAIN_EXISTS.equals(exception.getCode())) {
                throw exception;
            }
            runtime = vmControlPort.find(record.getVmId()).orElse(null);
        }
        if (runtime != null) {
            runtimeCache.put(record.getVmId(), runtime);
        }
        return Vm.of(record, runtime);
    }

    private VmRecord findRecord(String vmId) {
        return vmRecordRepository.findById(vmId)
                .filter(VmRecord::isRegistered)
                .orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "VM_NOT_FOUND", "존재하지 않는 VM입니다."));
    }

    private static String validateRequestKey(String requestKey) {
        if (requestKey == null || requestKey.isEmpty() || requestKey.length() > 200
                || requestKey.chars().anyMatch(character -> character < '!' || character > '~')) {
            throw new ApiException(
                    HttpStatus.BAD_REQUEST,
                    "INVALID_ARGUMENT",
                    "Idempotency-Key는 1~200자의 표시 가능한 ASCII 문자여야 합니다.");
        }
        return requestKey;
    }

    public record CreateResult(Vm vm, boolean created) {
    }
}
