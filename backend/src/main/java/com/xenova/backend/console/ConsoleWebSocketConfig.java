package com.xenova.backend.console;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

@Configuration
public class ConsoleWebSocketConfig implements WebSocketConfigurer {
    private final ConsoleWebSocketHandler handler;
    private final String[] origins;

    public ConsoleWebSocketConfig(ConsoleWebSocketHandler handler,
            @Value("${xenova.websocket.allowed-origins:*}") String[] origins) {
        this.handler = handler;
        this.origins = origins;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(handler, "/ws/v1/vms/*/console").setAllowedOriginPatterns(origins);
    }
}
