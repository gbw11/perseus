package com.perseus.backend.agent.infrastructure;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "perseus.mqtt")
public record MqttProperties(
        boolean enabled,
        String brokerUrl,
        String clientId,
        String username,
        String password,
        Duration reconnectInterval) {
}
