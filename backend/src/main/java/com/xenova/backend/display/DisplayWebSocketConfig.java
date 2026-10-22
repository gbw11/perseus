package com.xenova.backend.display;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

@Configuration
public class DisplayWebSocketConfig implements WebSocketConfigurer {
    private final DisplayStateWebSocket handler;
    private final String[] origins;

    public DisplayWebSocketConfig(DisplayStateWebSocket handler,
            @Value("${xenova.websocket.allowed-origins:*}") String[] origins) {
        this.handler = handler; this.origins = origins;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(handler, "/ws/v1/display/state").setAllowedOriginPatterns(origins);
    }
}
