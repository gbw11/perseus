package com.xenova.backend.display.websocket;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

// Registers WS /ws/v1/displays next to the other sockets (@EnableWebSocket is on the metrics WebSocketConfig).
@Configuration
public class DisplayWebSocketConfig implements WebSocketConfigurer {

    private final DisplayWebSocketHandler handler;
    private final String[] allowedOrigins;

    public DisplayWebSocketConfig(
            DisplayWebSocketHandler handler, @Value("${xenova.websocket.allowed-origins:*}") String[] allowedOrigins) {
        this.handler = handler;
        this.allowedOrigins = allowedOrigins;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(handler, "/ws/v1/displays").setAllowedOriginPatterns(allowedOrigins);
    }
}
