package com.xenova.backend.event.application;

import com.xenova.backend.event.domain.VmEvent;

// Published after an event is stored; WS-02 pushes it once the transaction commits.
public record EventRecorded(VmEvent event) {
}
