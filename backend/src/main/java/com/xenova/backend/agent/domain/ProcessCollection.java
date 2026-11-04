package com.xenova.backend.agent.domain;

/** Collection diagnostics reported with a Linux top-process snapshot. */
public record ProcessCollection(
        String status, int discovered, int readOk, int vanished, int failed, boolean truncated, String error) {
}
