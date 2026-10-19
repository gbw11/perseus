package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import java.time.Instant;

// Common wrapper of every agent message (MQTT-01/02/03/05).
record AgentEnvelope(
        String schemaVersion,
        String messageId,
        String vmId,
        String bootId,
        String agentInstanceId,
        String type,
        Long seq,
        Instant sentAt,
        JsonNode payload) {
}
