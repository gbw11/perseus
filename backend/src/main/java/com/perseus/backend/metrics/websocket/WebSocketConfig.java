package com.perseus.backend.metrics.websocket;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

@Configuration
@EnableWebSocket
public class WebSocketConfig implements WebSocketConfigurer {

    private final MetricsWebSocketHandler metricsHandler;
    private final String[] allowedOrigins;

    public WebSocketConfig(
            MetricsWebSocketHandler metricsHandler,
            @Value("${perseus.websocket.allowed-origins:*}") String[] allowedOrigins) {
        this.metricsHandler = metricsHandler;
        this.allowedOrigins = allowedOrigins;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(metricsHandler, "/ws/v1/metrics").setAllowedOriginPatterns(allowedOrigins);
    }
}
