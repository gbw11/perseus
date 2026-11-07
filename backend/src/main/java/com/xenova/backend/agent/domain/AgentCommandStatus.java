package com.xenova.backend.agent.domain;

public enum AgentCommandStatus {
    ACCEPTED,
    RUNNING,
    SUCCEEDED,
    FAILED,
    REJECTED;

    public boolean terminal() {
        return this != RUNNING;
    }
}
