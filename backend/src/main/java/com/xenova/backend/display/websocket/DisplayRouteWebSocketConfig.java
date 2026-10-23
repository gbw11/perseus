package com.xenova.backend.display.websocket;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

// Registers WS /ws/v1/displays (console) and WS /ws/v1/display-service (dom0 display service) next to the other
// sockets (@EnableWebSocket is on the metrics WebSocketConfig). Both stay open in Spring Security under /ws/**
// and authenticate with their first frame.
@Configuration
public class DisplayRouteWebSocketConfig implements WebSocketConfigurer {

    private final DisplayWebSocketHandler consoleHandler;
    private final DisplayServiceWebSocketHandler serviceHandler;
    private final String[] allowedOrigins;

    public DisplayRouteWebSocketConfig(
            DisplayWebSocketHandler consoleHandler, DisplayServiceWebSocketHandler serviceHandler,
            @Value("${xenova.websocket.allowed-origins:*}") String[] allowedOrigins) {
        this.consoleHandler = consoleHandler;
        this.serviceHandler = serviceHandler;
        this.allowedOrigins = allowedOrigins;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(consoleHandler, "/ws/v1/displays").setAllowedOriginPatterns(allowedOrigins);
        // The service is a local process without a browser Origin; the same pattern list still applies.
        registry.addHandler(serviceHandler, "/ws/v1/display-service").setAllowedOriginPatterns(allowedOrigins);
    }
}
