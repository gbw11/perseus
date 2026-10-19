package com.perseus.backend.agent.infrastructure;

import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

// Heartbeats arrive every 5s; ONLINE allows a few misses before the agent is treated as STALE then OFFLINE.
@ConfigurationProperties(prefix = "perseus.agent")
public record AgentProperties(Duration staleAfter, Duration offlineAfter) {
}
