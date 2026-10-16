package com.perseus.backend.template.domain;

public record VmTemplate(
        String templateId,
        String version,
        String displayName,
        int minMemoryMib) {
}
