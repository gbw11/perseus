package com.xenova.backend.vm.api;

import com.xenova.backend.vm.domain.VmRole;
import jakarta.validation.constraints.Pattern;

// PATCH /api/v1/vms/{vm_id}. Only the console-only role can change; the launch spec stays as created.
// {"role": null} clears the role.
public record UpdateVmRequest(
        @Pattern(regexp = VmRole.PATTERN, message = "role은 primary_drive, backup_drive, interaction 중 하나여야 합니다.")
        String role) {
}
