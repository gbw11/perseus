package com.xenova.backend.sim.websocket;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

// Registers WS-06 next to the drive sockets (@EnableWebSocket is already on the metrics WebSocketConfig).
@Configuration
public class SimWebSocketConfig implements WebSocketConfigurer {

    private final SimWebSocketHandler handler;
    private final String[] allowedOrigins;

    public SimWebSocketConfig(
            SimWebSocketHandler handler, @Value("${xenova.websocket.allowed-origins:*}") String[] allowedOrigins) {
        this.handler = handler;
        this.allowedOrigins = allowedOrigins;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(handler, "/ws/v1/sim").setAllowedOriginPatterns(allowedOrigins);
    }
}
