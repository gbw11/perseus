package com.xenova.backend.event.application;

import static org.assertj.core.api.Assertions.assertThat;

import com.xenova.backend.event.domain.EventSeverity;
import com.xenova.backend.event.domain.EventType;
import com.xenova.backend.event.domain.VmEvent;
import com.xenova.backend.event.domain.VmEventRepository;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest
class RecoveryEventPersistenceIntegrationTest {

    private static final Instant RECEIVED_AT = Instant.parse("2026-10-28T00:00:02Z");
    private static final String VM_ID = "00000000-0000-0000-0000-000000000002";
    private static final String RECOVERY_ID = "00000000-0000-0000-0000-000000000003";
    private static final String ATTEMPT_ID = "00000000-0000-0000-0000-000000000004";

    @Autowired
    private RecoveryEventMessageHandler handler;

    @Autowired
    private VmEventRepository repository;

    @BeforeEach
    void clearEvents() {
        repository.deleteAll();
    }

    @Test
    void repeatedDeliveryStoresOneRowAndReturnsDuplicate() {
        String eventId = eventId(101);
        byte[] payload = event(eventId, "recovery.started", "");

        assertThat(handler.handle(payload, RECEIVED_AT))
                .isEqualTo(RecoveryEventMessageHandler.Result.ACCEPTED);
        assertThat(handler.handle(payload, RECEIVED_AT.plusMillis(1)))
                .isEqualTo(RecoveryEventMessageHandler.Result.DUPLICATE);

        assertThat(repository.countByEventId(eventId)).isEqualTo(1);
    }

    @Test
    void concurrentDeliveryStoresOneRowAndReturnsOneDuplicate() throws Exception {
        String eventId = eventId(102);
        byte[] payload = event(eventId, "recovery.started", "");
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);

        try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            Future<RecoveryEventMessageHandler.Result> first = executor.submit(() ->
                    handleWhenReleased(payload, ready, start));
            Future<RecoveryEventMessageHandler.Result> second = executor.submit(() ->
                    handleWhenReleased(payload, ready, start));
            ready.await();
            start.countDown();

            assertThat(List.of(first.get(), second.get()))
                    .containsExactlyInAnyOrder(
                            RecoveryEventMessageHandler.Result.ACCEPTED,
                            RecoveryEventMessageHandler.Result.DUPLICATE);
        }

        assertThat(repository.countByEventId(eventId)).isEqualTo(1);
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("eventCases")
    void storesEveryTypeWithSeverityAndCorrelation(
            String name,
            int eventNumber,
            String externalType,
            String terminalFields,
            EventType storedType,
            EventSeverity severity) {
        String eventId = eventId(eventNumber);

        assertThat(handler.handle(event(eventId, externalType, terminalFields), RECEIVED_AT))
                .isEqualTo(RecoveryEventMessageHandler.Result.ACCEPTED);

        VmEvent stored = repository.findByEventId(eventId).orElseThrow();
        assertThat(stored.getType()).isEqualTo(storedType);
        assertThat(stored.getSeverity()).isEqualTo(severity);
        assertThat(stored.getVmId()).isEqualTo(VM_ID);
        assertThat(stored.getOperationId()).isEqualTo(RECOVERY_ID);
        assertThat(stored.getCorrelationId()).isEqualTo(ATTEMPT_ID);
        assertThat(stored.getOccurredAt()).isEqualTo(Instant.parse("2026-10-28T00:00:01Z"));
        assertThat(stored.getRecordedAt()).isEqualTo(RECEIVED_AT);
    }

    private RecoveryEventMessageHandler.Result handleWhenReleased(
            byte[] payload, CountDownLatch ready, CountDownLatch start) throws InterruptedException {
        ready.countDown();
        start.await();
        return handler.handle(payload, RECEIVED_AT);
    }

    private static Stream<Arguments> eventCases() {
        return Stream.of(
                Arguments.of("started", 201, "recovery.started", "",
                        EventType.RECOVERY_STARTED, EventSeverity.INFO),
                Arguments.of("completed", 202, "recovery.completed",
                        ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1000,\"execution_id\":\"exec-new\"",
                        EventType.RECOVERY_COMPLETED, EventSeverity.INFO),
                Arguments.of("recovered", 203, "recovery.recovered",
                        ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1000",
                        EventType.RECOVERY_RECOVERED, EventSeverity.INFO),
                Arguments.of("cancelled", 204, "recovery.cancelled",
                        ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1000,\"error\":" + error(),
                        EventType.RECOVERY_CANCELLED, EventSeverity.INFO),
                Arguments.of("retryable failed", 205, "recovery.failed",
                        ",\"attempt_duration_ms\":500,\"final\":false,"
                                + "\"next_retry_at\":\"2026-10-28T00:00:05Z\",\"error\":" + error(),
                        EventType.RECOVERY_FAILED, EventSeverity.WARNING),
                Arguments.of("final failed", 206, "recovery.failed",
                        ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1000,"
                                + "\"final\":true,\"error\":" + error(),
                        EventType.RECOVERY_FAILED, EventSeverity.ERROR));
    }

    private static byte[] event(String eventId, String type, String terminalFields) {
        return ("""
                {
                  "event_id":"%s",
                  "type":"%s",
                  "vm_id":"%s",
                  "recovery_id":"%s",
                  "recovery_attempt_id":"%s",
                  "attempt":1,
                  "reason":"HEARTBEAT_LOST",
                  "mode":"graceful-restart",
                  "detected_at":"2026-10-28T00:00:00Z",
                  "occurred_at":"2026-10-28T00:00:01Z"
                  %s
                }
                """).formatted(eventId, type, VM_ID, RECOVERY_ID, ATTEMPT_ID, terminalFields)
                .getBytes(StandardCharsets.UTF_8);
    }

    private static String eventId(int value) {
        return "00000000-0000-0000-0001-%012d".formatted(value);
    }

    private static String error() {
        return "{\"code\":\"XEN_EXECUTION_FAILED\",\"platform_code\":\"XEN_EXECUTION_FAILED\","
                + "\"message\":\"xl failed\"}";
    }
}
