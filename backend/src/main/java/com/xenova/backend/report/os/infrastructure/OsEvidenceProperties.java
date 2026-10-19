package com.xenova.backend.report.os.infrastructure;

import java.time.Duration;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "xenova.report.os-evidence")
public record OsEvidenceProperties(List<String> requiredOsIds, Duration observationWindow, String buildId) {

    public OsEvidenceProperties {
        requiredOsIds = requiredOsIds == null ? List.of() : List.copyOf(requiredOsIds);
    }
}
