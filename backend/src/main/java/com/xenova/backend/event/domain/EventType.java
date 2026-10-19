package com.xenova.backend.event.domain;

import com.fasterxml.jackson.annotation.JsonValue;
import java.util.Optional;

// Event kinds of REST-18 / WS-02. Recovery and demo kinds are listed so filters accept them
// before the features that produce them exist.
public enum EventType {
    VM_CREATED("vm.created"),
    VM_DELETED("vm.deleted"),
    VM_STATE_CHANGED("vm.state.changed"),
    AGENT_STATUS_CHANGED("agent.status.changed"),
    RECOVERY_STARTED("recovery.started"),
    RECOVERY_COMPLETED("recovery.completed"),
    RECOVERY_FAILED("recovery.failed"),
    DEMO_COMMAND_SENT("demo.command.sent"),
    DEMO_COMMAND_COMPLETED("demo.command.completed"),
    DEMO_COMMAND_FAILED("demo.command.failed");

    private final String value;

    EventType(String value) {
        this.value = value;
    }

    @JsonValue
    public String value() {
        return value;
    }

    public static Optional<EventType> fromValue(String value) {
        for (EventType type : values()) {
            if (type.value.equals(value)) {
                return Optional.of(type);
            }
        }
        return Optional.empty();
    }
}
