package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.xenova.backend.agent.domain.AgentCommandStatus;
import java.time.Instant;

public record AgentCommandStatusResponse(
        String vmId,
        String commandId,
        String operationId,
        AgentCommandStatus status,
        Instant submittedAt,
        Instant startedAt,
        Instant finishedAt,
        Long durationMs,
        JsonNode result,
        JsonNode error,
        Instant receivedAt,
        boolean contractViolation,
        String violationDetail) {
}
