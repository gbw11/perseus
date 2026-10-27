package com.xenova.backend.event.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import java.nio.charset.StandardCharsets;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.context.ApplicationEventPublisher;

class RecoveryEventMessageHandlerTest {

    @Test
    void publishesAnImmutableRawRecoveryEvent() {
        ApplicationEventPublisher publisher = mock(ApplicationEventPublisher.class);
        RecoveryEventMessageHandler handler = new RecoveryEventMessageHandler(publisher);
        byte[] payload = "event".getBytes(StandardCharsets.UTF_8);
        Instant receivedAt = Instant.parse("2026-10-27T06:00:00Z");

        handler.handle(payload, receivedAt);

        ArgumentCaptor<RecoveryEventReceived> captor = ArgumentCaptor.forClass(RecoveryEventReceived.class);
        verify(publisher).publishEvent(captor.capture());
        RecoveryEventReceived received = captor.getValue();
        assertThat(received.payload()).isEqualTo(payload);
        assertThat(received.receivedAt()).isEqualTo(receivedAt);

        payload[0] = 'X';
        assertThat(new String(received.payload(), StandardCharsets.UTF_8)).isEqualTo("event");
    }
}
