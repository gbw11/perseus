package com.perseus.backend.vm.api;

import com.perseus.backend.vm.domain.Vm;
import java.time.Instant;
import java.util.List;

// observed_at tells the client how fresh the list is, since it is read from dom0-manager on demand.
public record VmListResponse(List<Vm> items, Instant observedAt) {
}
