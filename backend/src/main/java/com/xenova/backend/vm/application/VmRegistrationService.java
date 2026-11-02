package com.xenova.backend.vm.application;

import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.api.VmRegistrationRequest;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import java.time.Clock;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
public class VmRegistrationService {

    private final VmRecordRepository repository;
    private final Clock clock;

    public VmRegistrationService(VmRecordRepository repository, Clock clock) {
        this.repository = repository;
        this.clock = clock;
    }

    @Transactional
    public Result register(VmRegistrationRequest request) {
        Validated value = validate(request);
        VmRecord existing = repository.findById(value.vmId()).orElse(null);
        if (existing != null) {
            if (!existing.isRegistered()) {
                throw conflict("보관 또는 삭제된 VM은 등록 통지로 복원할 수 없습니다.");
            }
            return new Result(existing.getVmId(), false);
        }
        repository.findByName(value.name()).ifPresent(conflict -> {
            throw conflict("다른 VM UUID가 같은 이름을 사용하고 있습니다.");
        });
        VmRecord created = VmRecord.registeredByPlatform(
                value.vmId(), value.name(), value.templateId(), value.templateVersion(),
                value.vcpus(), value.memoryMib(), value.guestKind(), clock.instant(), value.assignedIpv4());
        repository.save(created);
        return new Result(created.getVmId(), true);
    }

    private static Validated validate(VmRegistrationRequest request) {
        if (request == null || !"1.0".equals(request.schemaVersion())) {
            throw invalid("schema_version은 1.0이어야 합니다.");
        }
        if (!Boolean.TRUE.equals(request.ok())) {
            throw invalid("ok가 true인 성공 결과만 등록할 수 있습니다.");
        }
        parseUuid(request.requestId(), "request_id");
        if (request.data() == null || request.data().registration() == null) {
            throw invalid("data.registration이 필요합니다.");
        }

        VmRegistrationRequest.Registration registration = request.data().registration();
        if (!"1.0".equals(registration.schemaVersion())) {
            throw invalid("registration.schema_version은 1.0이어야 합니다.");
        }
        String vmId = parseUuid(registration.vmId(), "vm_id");
        String name = clean(registration.name(), 63, "name");
        String templateId = clean(registration.templateId(), 64, "template_id");
        String templateVersion = registration.templateVersion() == null
                ? null : clean(registration.templateVersion(), 64, "template_version");
        GuestKind guestKind = switch (registration.guestKind() == null ? "" : registration.guestKind()) {
            case "linux" -> GuestKind.LINUX;
            case "zephyr" -> GuestKind.ZEPHYR;
            default -> throw invalid("guest_kind는 linux 또는 zephyr여야 합니다.");
        };
        int vcpus = bounded(registration.vcpus(), 1, 4, "vcpus");
        int memoryMib = bounded(registration.memoryMib(), 16, 8192, "memory_mib");
        String assignedIpv4 = validateIpv4(registration.assignedIpv4());
        return new Validated(vmId, name, guestKind, templateId, templateVersion, vcpus, memoryMib, assignedIpv4);
    }

    private static String parseUuid(String value, String field) {
        try {
            return UUID.fromString(value).toString();
        } catch (RuntimeException exception) {
            throw invalid(field + "는 UUID 형식이어야 합니다.");
        }
    }

    private static String clean(String value, int max, String field) {
        if (value == null || value.isBlank() || !value.equals(value.strip()) || value.length() > max
                || value.chars().anyMatch(Character::isISOControl)) {
            throw invalid(field + " 값이 올바르지 않습니다.");
        }
        return value;
    }

    private static int bounded(Integer value, int min, int max, String field) {
        if (value == null || value < min || value > max) {
            throw invalid(field + " 값이 올바르지 않습니다.");
        }
        return value;
    }

    private static String validateIpv4(String value) {
        if (value == null) {
            return null;
        }
        String[] parts = value.split("\\.", -1);
        if (parts.length != 4) {
            throw invalid("assigned_ipv4는 정규 IPv4 주소여야 합니다.");
        }
        for (String part : parts) {
            if (part.isEmpty() || part.length() > 3 || !part.chars().allMatch(Character::isDigit)
                    || (part.length() > 1 && part.startsWith("0")) || Integer.parseInt(part) > 255) {
                throw invalid("assigned_ipv4는 정규 IPv4 주소여야 합니다.");
            }
        }
        return value;
    }

    private static ApiException invalid(String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, "INVALID_REGISTRATION", message);
    }

    private static ApiException conflict(String message) {
        return new ApiException(HttpStatus.CONFLICT, "VM_REGISTRATION_CONFLICT", message);
    }

    public record Result(String vmId, boolean created) {
    }

    private record Validated(
            String vmId,
            String name,
            GuestKind guestKind,
            String templateId,
            String templateVersion,
            int vcpus,
            int memoryMib,
            String assignedIpv4) {
    }
}
