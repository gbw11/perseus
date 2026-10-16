package com.perseus.backend.vm.api;

import com.perseus.backend.vm.domain.CreateVmCommand;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record CreateVmRequest(
        @NotBlank(message = "name은 필수입니다.")
        @Pattern(regexp = "^[a-z0-9-]{1,63}$", message = "name은 1~63자의 소문자, 숫자, -만 사용할 수 있습니다.")
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
        @Min(value = 100, message = "memory_mib는 100~1024여야 합니다.")
        @Max(value = 1024, message = "memory_mib는 100~1024여야 합니다.")
        Integer memoryMib) {

    CreateVmCommand toCommand() {
        return new CreateVmCommand(name, templateId, templateVersion, vcpus, memoryMib);
    }
}
