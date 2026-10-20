package com.xenova.backend.agent.infrastructure;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "xenova.mqtt")
public record MqttProperties(
        boolean enabled,
        String brokerUrl,
        String clientId,
        String username,
        String password,
        Duration reconnectInterval) {
}
