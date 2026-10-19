package com.perseus.backend.vm.infrastructure;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "perseus.dom0-manager")
public record Dom0ManagerProperties(
        String baseUrl,
        String serviceToken,
        Duration connectTimeout,
        Duration queryTimeout,
        Duration commandTimeout,
        Duration retryBackoff) {
}
