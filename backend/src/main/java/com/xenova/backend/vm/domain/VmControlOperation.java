package com.xenova.backend.vm.domain;

import java.util.List;

/** Public-safe projection of dom0-manager's tracked stop/reboot operation. */
public record VmControlOperation(
        String vmId,
        String action,
        String requestKey,
        String operationId,
        String status,
        Boolean accepted,
        String phase,
        Long intentRevision,
        String hostBootId,
        Long lifecycleGeneration,
        Long deadlineMonotonicMs,
        Boolean executionDeadlineExpired,
        String nextAction,
        String errorCode,
        List<String> warnings) {
}
