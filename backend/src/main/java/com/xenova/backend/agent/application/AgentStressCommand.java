package com.xenova.backend.agent.application;

import java.time.Instant;

public record AgentStressCommand(
        String commandId,
        String operationId,
        String vmId,
        StressType type,
        int amount,
        Integer loadPercent,
        int durationS,
        Instant submittedAt,
        Instant expiresAt) {

    public enum StressType {
        CPU,
        MEMORY
    }
}
