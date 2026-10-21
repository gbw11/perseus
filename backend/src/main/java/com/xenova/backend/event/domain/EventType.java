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
    DEMO_COMMAND_FAILED("demo.command.failed"),
    // MQTT contract §6: recorded by the drive relay (S15P31E110-154) from the gateway's app events.
    DRIVE_MODE_CHANGED("drive.mode.changed"),
    DRIVE_FAULT_DETECTED("drive.fault.detected"),
    // The gateway accepted / cancelled a web resume request (MQTT-13, S15P31E110-154).
    DRIVE_RESUME_ACCEPTED("drive.resume.accepted"),
    DRIVE_RESUME_CANCELLED("drive.resume.cancelled"),
    // A console changed what a monitor should show (REST-24, S15P31E110-129).
    DISPLAY_ROUTE_CHANGED("display.route.changed");

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
