package com.xenova.backend.event.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.xenova.backend.event.domain.RecoverySyncState;
import com.xenova.backend.event.domain.RecoverySyncStateRepository;
import com.xenova.backend.event.infrastructure.RecoveryEventsClient;
import com.xenova.backend.event.infrastructure.RecoveryEventsClient.RecoveryEventsClientException;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

class RecoveryEventBackfillServiceTest {

    private static final Instant NOW = Instant.parse("2026-10-28T00:00:00Z");
    private static final UUID EVENT_ID = UUID.fromString("00000000-0000-0000-0000-000000000001");

    private final RecoveryEventsClient client = mock(RecoveryEventsClient.class);
    private final RecoveryEventMessageHandler handler = mock(RecoveryEventMessageHandler.class);
    private final RecoverySyncStateRepository repository = mock(RecoverySyncStateRepository.class);
    private final RecoveryEventBackfillService service = new RecoveryEventBackfillService(
            client, handler, repository, Clock.fixed(NOW, ZoneOffset.UTC));

    @Test
    void processesPagesAndAdvancesOnlyToNextCursor() {
        RecoveryEvent event = event(EVENT_ID);
        when(repository.findById(RecoverySyncState.RECOVERY_EVENTS)).thenReturn(Optional.empty());
        when(client.fetch(null, RecoveryEventBackfillService.PAGE_SIZE))
                .thenReturn(new RecoveryEventsClient.Page(List.of(event), EVENT_ID, EVENT_ID, false));
        when(client.fetch(EVENT_ID.toString(), RecoveryEventBackfillService.PAGE_SIZE))
                .thenReturn(new RecoveryEventsClient.Page(List.of(), null, EVENT_ID, false));
        when(handler.handle(event, NOW)).thenReturn(RecoveryEventMessageHandler.Result.ACCEPTED);

        service.backfill();

        verify(handler).handle(event, NOW);
        verify(client).fetch(EVENT_ID.toString(), RecoveryEventBackfillService.PAGE_SIZE);
        ArgumentCaptor<RecoverySyncState> captor = ArgumentCaptor.forClass(RecoverySyncState.class);
        verify(repository).saveAndFlush(captor.capture());
        assertThat(captor.getValue().getAfterEventId()).isEqualTo(EVENT_ID.toString());
    }

    @Test
    void rejectedEventDoesNotAdvanceCursor() {
        RecoveryEvent event = event(EVENT_ID);
        when(repository.findById(RecoverySyncState.RECOVERY_EVENTS)).thenReturn(Optional.empty());
        when(client.fetch(null, RecoveryEventBackfillService.PAGE_SIZE))
                .thenReturn(new RecoveryEventsClient.Page(List.of(event), EVENT_ID, EVENT_ID, false));
        when(handler.handle(event, NOW)).thenReturn(RecoveryEventMessageHandler.Result.REJECTED);

        service.backfill();

        verify(repository, never()).saveAndFlush(org.mockito.ArgumentMatchers.any());
    }

    @Test
    void unavailableResponseKeepsExistingCursor() {
        RecoverySyncState state = new RecoverySyncState(RecoverySyncState.RECOVERY_EVENTS, NOW);
        state.advance(EVENT_ID.toString(), NOW);
        when(repository.findById(RecoverySyncState.RECOVERY_EVENTS)).thenReturn(Optional.of(state));
        when(client.fetch(EVENT_ID.toString(), RecoveryEventBackfillService.PAGE_SIZE))
                .thenThrow(new RecoveryEventsClientException(503));

        service.backfill();

        verify(repository, never()).saveAndFlush(org.mockito.ArgumentMatchers.any());
        assertThat(state.getAfterEventId()).isEqualTo(EVENT_ID.toString());
    }

    private static RecoveryEvent event(UUID eventId) {
        return new RecoveryEvent(
                eventId,
                RecoveryEvent.Type.STARTED,
                UUID.fromString("00000000-0000-0000-0000-000000000002"),
                UUID.fromString("00000000-0000-0000-0000-000000000003"),
                UUID.fromString("00000000-0000-0000-0000-000000000004"),
                1,
                "HEARTBEAT_LOST",
                "graceful-restart",
                NOW,
                NOW,
                null,
                null,
                null,
                null,
                null,
                null);
    }
}
