package com.xenova.backend.agent.domain;

import java.time.Instant;

public record AgentCommandResultReport(
        String messageId,
        String bootId,
        String agentInstanceId,
        long seq,
        String commandId,
        String operationId,
        AgentCommandStatus status,
        Instant startedAt,
        Instant finishedAt,
        Long durationMs,
        String resultJson,
        String errorJson,
        Instant receivedAt) {
}
