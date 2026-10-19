package com.xenova.backend.agent.domain;

// Judged by the receiver from heartbeat age; the agent never declares its own status.
public enum AgentStatus {
    UNKNOWN,
    ONLINE,
    STALE,
    OFFLINE
}
