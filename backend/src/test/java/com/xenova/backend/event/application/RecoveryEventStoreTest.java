package com.xenova.backend.event.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.event.domain.VmEvent;
import com.xenova.backend.event.domain.VmEventRepository;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.context.ApplicationEventPublisher;

class RecoveryEventStoreTest {

    private static final Instant RECEIVED_AT = Instant.parse("2026-10-27T06:00:00Z");
    private static final UUID EVENT_ID = UUID.fromString("00000000-0000-0000-0000-000000000001");
    private static final UUID VM_ID = UUID.fromString("00000000-0000-0000-0000-000000000002");
    private static final UUID RECOVERY_ID = UUID.fromString("00000000-0000-0000-0000-000000000003");
    private static final UUID ATTEMPT_ID = UUID.fromString("00000000-0000-0000-0000-000000000004");

    private final VmEventRepository repository = mock(VmEventRepository.class);
    private final ApplicationEventPublisher publisher = mock(ApplicationEventPublisher.class);
    private final RecoveryEventStore store = new RecoveryEventStore(repository, publisher);

    @Test
    void storesExternalIdentityCorrelationSeverityAndPayload() {
        when(repository.saveAndFlush(any())).thenAnswer(invocation -> invocation.getArgument(0));
        RecoveryEvent source = event(RecoveryEvent.Type.FAILED, false);

        assertThat(store.store(source, RECEIVED_AT)).isEqualTo(RecoveryEventStore.StoreResult.STORED);

        ArgumentCaptor<VmEvent> captor = ArgumentCaptor.forClass(VmEvent.class);
        verify(repository).saveAndFlush(captor.capture());
        VmEvent saved = captor.getValue();
        assertThat(saved.getEventId()).isEqualTo(EVENT_ID.toString());
        assertThat(saved.getType()).isEqualTo(EventType.RECOVERY_FAILED);
        assertThat(saved.getSeverity()).isEqualTo(EventSeverity.WARNING);
        assertThat(saved.getVmId()).isEqualTo(VM_ID.toString());
        assertThat(saved.getOperationId()).isEqualTo(RECOVERY_ID.toString());
        assertThat(saved.getCorrelationId()).isEqualTo(ATTEMPT_ID.toString());
        assertThat(saved.getOccurredAt()).isEqualTo(Instant.parse("2026-10-23T01:00:01Z"));
        assertThat(saved.getRecordedAt()).isEqualTo(RECEIVED_AT);
        assertThat(saved.getPayload()).containsEntry("attempt", 1).containsEntry("final", false);
        verify(publisher).publishEvent(any(EventRecorded.class));
    }

    @Test
    void finalFailureIsError() {
        when(repository.saveAndFlush(any())).thenAnswer(invocation -> invocation.getArgument(0));

        store.store(event(RecoveryEvent.Type.FAILED, true), RECEIVED_AT);

        ArgumentCaptor<VmEvent> captor = ArgumentCaptor.forClass(VmEvent.class);
        verify(repository).saveAndFlush(captor.capture());
        assertThat(captor.getValue().getSeverity()).isEqualTo(EventSeverity.ERROR);
    }

    @Test
    void knownEventIdIsIgnoredBeforeInsert() {
        when(repository.existsByEventId(EVENT_ID.toString())).thenReturn(true);

        assertThat(store.store(event(RecoveryEvent.Type.STARTED, null), RECEIVED_AT))
                .isEqualTo(RecoveryEventStore.StoreResult.DUPLICATE);

        verify(repository, never()).saveAndFlush(any());
        verify(publisher, never()).publishEvent(any());
    }

    private static RecoveryEvent event(RecoveryEvent.Type type, Boolean finalAttempt) {
        boolean failed = type == RecoveryEvent.Type.FAILED;
        return new RecoveryEvent(
                EVENT_ID,
                type,
                VM_ID,
                RECOVERY_ID,
                ATTEMPT_ID,
                1,
                "HEARTBEAT_LOST",
                "graceful-restart",
                Instant.parse("2026-10-23T01:00:00Z"),
                Instant.parse("2026-10-23T01:00:01Z"),
                failed ? 500L : null,
                Boolean.TRUE.equals(finalAttempt) ? 1000L : null,
                null,
                finalAttempt,
                Boolean.FALSE.equals(finalAttempt) ? Instant.parse("2026-10-23T01:00:05Z") : null,
                failed ? new RecoveryEvent.Error("FAILED", "XEN_FAILED", "xl failed") : null);
    }
}
