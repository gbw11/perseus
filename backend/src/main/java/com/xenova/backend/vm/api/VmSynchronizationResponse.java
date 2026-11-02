package com.xenova.backend.vm.api;

import java.util.List;

public record VmSynchronizationResponse(
        int discovered,
        int registered,
        int updated,
        int unchanged,
        int conflicts,
        int incomplete,
        int missingFromBoard,
        List<Item> results) {

    public record Item(String vmId, String action, String reason) {
    }
}
