package com.xenova.backend.agent.domain;

public enum AgentCommandStatus {
    PENDING,
    ACCEPTED,
    RUNNING,
    SUCCEEDED,
    FAILED,
    REJECTED;

    public boolean terminal() {
        return this != PENDING && this != RUNNING;
    }
}
