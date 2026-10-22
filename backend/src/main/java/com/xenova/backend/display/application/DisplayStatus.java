package com.xenova.backend.display.application;

// Judged from the dom0 display service (WS /ws/v1/display-service). DISCONNECTED: no service, or it reports the
// monitor unplugged. IDLE: service said hello but route.set for this display is not out yet. SWITCHING: route.set
// sent, current revision not confirmed. ACTIVE: route.applied for the current revision. ERROR: route.failed.
public enum DisplayStatus {
    DISCONNECTED,
    IDLE,
    SWITCHING,
    ACTIVE,
    ERROR
}
