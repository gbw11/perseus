package com.xenova.backend.agent.application;

import java.time.Instant;

public record AutoDelayCommand(
        String commandId,
        String operationId,
        String vmId,
        int delayMs,
        int durationS,
        Instant submittedAt,
        Instant expiresAt) {
}
