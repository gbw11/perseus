package com.perseus.backend.agent.domain;

import java.time.Instant;
import java.util.List;

public record InventoryReport(
        String bootId,
        String osId,
        String osVersionId,
        String prettyName,
        String kernelRelease,
        String hostname,
        List<NetworkInterface> interfaces,
        Instant observedAt) {
}
