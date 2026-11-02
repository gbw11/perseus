package com.xenova.backend.event.application;

import com.xenova.backend.event.domain.RecoverySyncState;
import com.xenova.backend.event.domain.RecoverySyncStateRepository;
import com.xenova.backend.event.infrastructure.RecoveryEventsClient;
import com.xenova.backend.event.infrastructure.RecoveryEventsClient.RecoveryEventsClientException;
import com.xenova.backend.vm.infrastructure.Dom0ManagerProperties;
import jakarta.annotation.PreDestroy;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

@Service
public class RecoveryEventBackfillService {

    static final int PAGE_SIZE = 200;
    static final Duration MAX_RETRY_BACKOFF = Duration.ofSeconds(30);

    private static final Logger log = LoggerFactory.getLogger(RecoveryEventBackfillService.class);

    private final RecoveryEventsClient client;
    private final RecoveryEventMessageHandler handler;
    private final RecoverySyncStateRepository syncStateRepository;
    private final Clock clock;
    private final Duration retryBackoff;
    private final ScheduledExecutorService executor = Executors.newSingleThreadScheduledExecutor(runnable -> {
        Thread thread = new Thread(runnable, "recovery-event-backfill");
        thread.setDaemon(true);
        return thread;
    });
    private final AtomicBoolean requested = new AtomicBoolean();
    private final AtomicBoolean running = new AtomicBoolean();
    private final AtomicInteger retryAttempt = new AtomicInteger();
    private final Object retryMonitor = new Object();
    private ScheduledFuture<?> pendingRetry;

    public RecoveryEventBackfillService(
            RecoveryEventsClient client,
            RecoveryEventMessageHandler handler,
            RecoverySyncStateRepository syncStateRepository,
            Clock clock,
            Dom0ManagerProperties properties) {
        this.client = client;
        this.handler = handler;
        this.syncStateRepository = syncStateRepository;
        this.clock = clock;
        this.retryBackoff = properties.retryBackoff();
    }

    public void requestBackfill() {
        retryAttempt.set(0);
        synchronized (retryMonitor) {
            if (pendingRetry != null) {
                pendingRetry.cancel(false);
                pendingRetry = null;
            }
        }
        enqueueBackfill();
    }

    private void enqueueBackfill() {
        requested.set(true);
        if (running.compareAndSet(false, true)) {
            executor.execute(this::runRequestedBackfills);
        }
    }

    private void runRequestedBackfills() {
        try {
            do {
                requested.set(false);
                BackfillResult result = backfill();
                if (result == BackfillResult.RETRYABLE_FAILURE) {
                    scheduleRetry();
                    return;
                }
                retryAttempt.set(0);
            } while (requested.get());
        } catch (RuntimeException exception) {
            log.error("Recovery event backfill failed unexpectedly", exception);
        } finally {
            running.set(false);
            if (requested.get()) {
                requestBackfill();
            }
        }
    }

    BackfillResult backfill() {
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
                return exception.isRetryable()
                        ? BackfillResult.RETRYABLE_FAILURE
                        : BackfillResult.TERMINAL_FAILURE;
            }
            if (page.gap()) {
                log.warn("Recovery event history has a gap after cursor {}; oldest retained event is {}",
                        cursor, page.oldestEventId());
            }
            if (page.events().isEmpty()) {
                return BackfillResult.COMPLETED;
            }

            Instant receivedAt = clock.instant();
            for (RecoveryEvent event : page.events()) {
                RecoveryEventMessageHandler.Result result = handler.handle(event, receivedAt);
                if (result == RecoveryEventMessageHandler.Result.REJECTED) {
                    log.error("Recovery event backfill stopped because event {} was rejected", event.eventId());
                    return BackfillResult.TERMINAL_FAILURE;
                }
            }

            UUID next = page.nextAfterEventId();
            if (next == null || next.toString().equals(cursor)) {
                log.error("Recovery event backfill returned a non-advancing cursor after {} event(s)",
                        page.events().size());
                return BackfillResult.TERMINAL_FAILURE;
            }
            cursor = next.toString();
            advanceCursor(cursor, clock.instant());
        }
    }

    private void scheduleRetry() {
        long delayMillis = retryDelayMillis(retryAttempt.getAndIncrement());
        log.warn("Recovery event backfill will retry in {} ms with the persisted cursor", delayMillis);
        synchronized (retryMonitor) {
            pendingRetry = executor.schedule(() -> {
                synchronized (retryMonitor) {
                    pendingRetry = null;
                }
                enqueueBackfill();
            }, delayMillis, TimeUnit.MILLISECONDS);
        }
    }

    long retryDelayMillis(int attempt) {
        long baseMillis = Math.max(1, retryBackoff.toMillis());
        long maxMillis = MAX_RETRY_BACKOFF.toMillis();
        long multiplier = 1L << Math.min(attempt, 30);
        return baseMillis > maxMillis / multiplier
                ? maxMillis
                : Math.min(baseMillis * multiplier, maxMillis);
    }

    private void advanceCursor(String cursor, Instant updatedAt) {
        RecoverySyncState state = syncStateRepository.findById(RecoverySyncState.RECOVERY_EVENTS)
                .orElseGet(() -> new RecoverySyncState(RecoverySyncState.RECOVERY_EVENTS, updatedAt));
        state.advance(cursor, updatedAt);
        syncStateRepository.saveAndFlush(state);
    }

    @PreDestroy
    void stop() {
        synchronized (retryMonitor) {
            if (pendingRetry != null) {
                pendingRetry.cancel(false);
                pendingRetry = null;
            }
        }
        executor.shutdownNow();
    }

    enum BackfillResult {
        COMPLETED,
        RETRYABLE_FAILURE,
        TERMINAL_FAILURE
    }
}
