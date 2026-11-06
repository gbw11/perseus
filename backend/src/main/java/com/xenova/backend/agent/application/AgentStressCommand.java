package com.xenova.backend.agent.application;

import java.time.Instant;

public record AgentStressCommand(
        String commandId,
        String operationId,
        String vmId,
        StressType type,
        int amount,
        int durationS,
        Instant submittedAt,
        Instant expiresAt) {

    public enum StressType {
        CPU,
        MEMORY
    }
}
