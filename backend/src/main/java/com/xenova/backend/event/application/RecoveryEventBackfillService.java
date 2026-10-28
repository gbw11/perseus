package com.xenova.backend.event.application;

import com.xenova.backend.event.domain.RecoverySyncState;
import com.xenova.backend.event.domain.RecoverySyncStateRepository;
import com.xenova.backend.event.infrastructure.RecoveryEventsClient;
import com.xenova.backend.event.infrastructure.RecoveryEventsClient.RecoveryEventsClientException;
import jakarta.annotation.PreDestroy;
import java.time.Clock;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

@Service
public class RecoveryEventBackfillService {

    static final int PAGE_SIZE = 200;

    private static final Logger log = LoggerFactory.getLogger(RecoveryEventBackfillService.class);

    private final RecoveryEventsClient client;
    private final RecoveryEventMessageHandler handler;
    private final RecoverySyncStateRepository syncStateRepository;
    private final Clock clock;
    private final ExecutorService executor = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "recovery-event-backfill");
        thread.setDaemon(true);
        return thread;
    });
    private final AtomicBoolean requested = new AtomicBoolean();
    private final AtomicBoolean running = new AtomicBoolean();

    public RecoveryEventBackfillService(
            RecoveryEventsClient client,
            RecoveryEventMessageHandler handler,
            RecoverySyncStateRepository syncStateRepository,
            Clock clock) {
        this.client = client;
        this.handler = handler;
        this.syncStateRepository = syncStateRepository;
        this.clock = clock;
    }

    public void requestBackfill() {
        requested.set(true);
        if (running.compareAndSet(false, true)) {
            executor.execute(this::runRequestedBackfills);
        }
    }

    private void runRequestedBackfills() {
        try {
            do {
                requested.set(false);
                backfill();
            } while (requested.get());
        } finally {
            running.set(false);
            if (requested.get()) {
                requestBackfill();
            }
        }
    }

    void backfill() {
        String cursor = syncStateRepository.findById(RecoverySyncState.RECOVERY_EVENTS)
                .map(RecoverySyncState::getAfterEventId)
                .orElse(null);
        while (true) {
            RecoveryEventsClient.Page page;
            try {
                page = client.fetch(cursor, PAGE_SIZE);
            } catch (RecoveryEventsClientException exception) {
                log.warn("Recovery event backfill stopped at cursor {} (status={}, retryable={})",
                        cursor, exception.getStatus(), exception.isRetryable());
                return;
            }
            if (page.gap()) {
                log.warn("Recovery event history has a gap after cursor {}; oldest retained event is {}",
                        cursor, page.oldestEventId());
            }
            if (page.events().isEmpty()) {
                return;
            }

            Instant receivedAt = clock.instant();
            for (RecoveryEvent event : page.events()) {
                RecoveryEventMessageHandler.Result result = handler.handle(event, receivedAt);
                if (result == RecoveryEventMessageHandler.Result.REJECTED) {
                    log.error("Recovery event backfill stopped because event {} was rejected", event.eventId());
                    return;
                }
            }

            UUID next = page.nextAfterEventId();
            if (next == null || next.toString().equals(cursor)) {
                log.error("Recovery event backfill returned a non-advancing cursor after {} event(s)",
                        page.events().size());
                return;
            }
            cursor = next.toString();
            advanceCursor(cursor, clock.instant());
        }
    }

    private void advanceCursor(String cursor, Instant updatedAt) {
        RecoverySyncState state = syncStateRepository.findById(RecoverySyncState.RECOVERY_EVENTS)
                .orElseGet(() -> new RecoverySyncState(RecoverySyncState.RECOVERY_EVENTS, updatedAt));
        state.advance(cursor, updatedAt);
        syncStateRepository.saveAndFlush(state);
    }

    @PreDestroy
    void stop() {
        executor.shutdownNow();
    }
}
