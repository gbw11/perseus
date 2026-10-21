package com.xenova.backend.drive.websocket;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

// Registers the drive relay sockets next to WS-01/WS-02 (another WebSocketConfigurer; @EnableWebSocket is
// already on the metrics WebSocketConfig).
@Configuration
public class DriveWebSocketConfig implements WebSocketConfigurer {

    private final DriveStateWebSocketHandler stateHandler;
    private final DriveInputWebSocketHandler inputHandler;
    private final String[] allowedOrigins;

    public DriveWebSocketConfig(
            DriveStateWebSocketHandler stateHandler,
            DriveInputWebSocketHandler inputHandler,
            @Value("${xenova.websocket.allowed-origins:*}") String[] allowedOrigins) {
        this.stateHandler = stateHandler;
        this.inputHandler = inputHandler;
        this.allowedOrigins = allowedOrigins;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(stateHandler, "/ws/v1/drive/state").setAllowedOriginPatterns(allowedOrigins);
        registry.addHandler(inputHandler, "/ws/v1/drive").setAllowedOriginPatterns(allowedOrigins);
    }
}
