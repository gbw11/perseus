package com.xenova.backend.vm.api;

import java.time.Instant;
import java.util.List;

// observed_at tells the client how fresh the list is, since it is read from dom0-manager on demand.
public record VmListResponse(List<VmResponse> items, Instant observedAt) {
}
