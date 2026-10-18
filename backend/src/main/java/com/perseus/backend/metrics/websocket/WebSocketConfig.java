package com.perseus.backend.metrics.websocket;

import com.perseus.backend.event.websocket.EventsWebSocketHandler;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.socket.config.annotation.EnableWebSocket;
import org.springframework.web.socket.config.annotation.WebSocketConfigurer;
import org.springframework.web.socket.config.annotation.WebSocketHandlerRegistry;

@Configuration
@EnableWebSocket
public class WebSocketConfig implements WebSocketConfigurer {

    private final MetricsWebSocketHandler metricsHandler;
    private final EventsWebSocketHandler eventsHandler;
    private final String[] allowedOrigins;

    public WebSocketConfig(
            MetricsWebSocketHandler metricsHandler,
            EventsWebSocketHandler eventsHandler,
            @Value("${perseus.websocket.allowed-origins:*}") String[] allowedOrigins) {
        this.metricsHandler = metricsHandler;
        this.eventsHandler = eventsHandler;
        this.allowedOrigins = allowedOrigins;
    }

    @Override
    public void registerWebSocketHandlers(WebSocketHandlerRegistry registry) {
        registry.addHandler(metricsHandler, "/ws/v1/metrics").setAllowedOriginPatterns(allowedOrigins);
        registry.addHandler(eventsHandler, "/ws/v1/events").setAllowedOriginPatterns(allowedOrigins);
    }
}
