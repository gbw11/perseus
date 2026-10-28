package com.xenova.backend.vm.api;

import com.xenova.backend.vm.domain.CreateVmCommand;
import com.xenova.backend.vm.domain.VmRole;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

// name은 표시 이름이며 dom0-manager를 거쳐 플랫폼 create에 전달한다. 실행 이름·경로는 UUID로 결정한다.
// Control and line-separator characters and leading/trailing whitespace are refused so the name stays one clean line.
public record CreateVmRequest(
        @NotBlank(message = "name은 필수입니다.")
        @Pattern(
                regexp = "^(?!\\s)(?!.*\\s$)[^\\p{Cc}\\p{Zl}\\p{Zp}]{1,63}$",
                message = "name은 1~63자이며 제어문자와 앞뒤 공백은 사용할 수 없습니다.")
        String name,
        @NotBlank(message = "template_id는 필수입니다.")
        @Size(max = 64, message = "template_id는 64자 이하여야 합니다.")
        String templateId,
        @NotBlank(message = "template_version은 필수입니다.")
        @Size(max = 64, message = "template_version은 64자 이하여야 합니다.")
        String templateVersion,
        @NotNull(message = "vcpus는 필수입니다.")
        @Min(value = 1, message = "vcpus는 1~4여야 합니다.")
        @Max(value = 4, message = "vcpus는 1~4여야 합니다.")
        Integer vcpus,
        @NotNull(message = "memory_mib는 필수입니다.")
        // 16 is only a loose common floor for the fixed 16MiB Zephyr starter profile; each template's
        // min_memory_mib is the real lower bound (Linux templates stay at 256/512).
        @Min(value = 16, message = "memory_mib는 16~1024여야 합니다.")
        @Max(value = 1024, message = "memory_mib는 16~1024여야 합니다.")
        Integer memoryMib,
        // Optional deployment binding. It is independent of autonomous/manual app ids within a drive VM.
        @Pattern(regexp = VmRole.PATTERN, message = "role은 primary_drive, backup_drive, interaction 중 하나여야 합니다.")
        String role) {

    CreateVmCommand toCommand() {
        return new CreateVmCommand(name, templateId, templateVersion, vcpus, memoryMib, VmRole.fromValue(role));
    }
}
