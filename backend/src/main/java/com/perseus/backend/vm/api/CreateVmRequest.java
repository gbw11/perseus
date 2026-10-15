package com.perseus.backend.vm.api;

import com.perseus.backend.vm.domain.CreateVmCommand;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

public record CreateVmRequest(
        @NotBlank @Size(max = 64) @Pattern(regexp = "[A-Za-z0-9][A-Za-z0-9._-]*") String name,
        @NotBlank @Size(max = 64) String templateId,
        @NotBlank @Size(max = 64) String templateVersion,
        @Min(1) @Max(256) int vcpus,
        @Min(1) int memoryMib) {

    CreateVmCommand toCommand() {
        return new CreateVmCommand(name, templateId, templateVersion, vcpus, memoryMib);
    }
}
