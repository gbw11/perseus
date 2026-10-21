package com.xenova.backend.display.application;

// DISCONNECTED: no monitor socket said hello for this display. SWITCHING: the monitor has not yet confirmed the
// current revision. ACTIVE: it has. IDLE and ERROR are reserved for the dom0 display service (not produced yet).
public enum DisplayStatus {
    DISCONNECTED,
    IDLE,
    SWITCHING,
    ACTIVE,
    ERROR
}
