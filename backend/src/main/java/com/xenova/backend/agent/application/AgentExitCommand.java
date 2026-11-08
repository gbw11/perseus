package com.xenova.backend.agent.application;

import java.time.Instant;

public record AgentExitCommand(
        String commandId,
        String operationId,
        String vmId,
        Instant submittedAt,
        Instant expiresAt) {
}
