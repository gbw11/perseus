package com.xenova.backend.display.websocket;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.display.application.DisplayRegistry;
import org.junit.jupiter.api.Test;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketSession;

// Without xenova.display.service-token every display service connection is refused (4401), even a well-formed auth.
class DisplayServiceWebSocketHandlerTest {

    @Test
    void emptyServiceTokenRefusesEveryConnection() throws Exception {
        DisplayRegistry registry = mock(DisplayRegistry.class);
        DisplayServiceWebSocketHandler handler = new DisplayServiceWebSocketHandler(new ObjectMapper(), registry, "");
        WebSocketSession session = mock(WebSocketSession.class);
        when(session.getId()).thenReturn("s1");
        when(session.isOpen()).thenReturn(true);

        handler.afterConnectionEstablished(session);
        handler.handleMessage(session, new TextMessage("{\"type\":\"auth\",\"service_token\":\"\"}"));

        verify(session).close(DisplayServiceWebSocketHandler.UNAUTHORIZED);
        verify(registry, never()).serviceConnected(any());
    }
}
