package com.xenova.backend.event.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.reset;
import static org.mockito.Mockito.verify;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.stream.Stream;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.mockito.ArgumentCaptor;
import org.springframework.context.ApplicationEventPublisher;

class RecoveryEventMessageHandlerTest {

    private static final Instant RECEIVED_AT = Instant.parse("2026-10-27T06:00:00Z");

    private ApplicationEventPublisher publisher;
    private RecoveryEventMessageHandler handler;

    @BeforeEach
    void setUp() {
        ObjectMapper objectMapper = JsonMapper.builder()
                .addModule(new JavaTimeModule())
                .propertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
                .build();
        publisher = mock(ApplicationEventPublisher.class);
        handler = new RecoveryEventMessageHandler(objectMapper, publisher);
    }

    @ParameterizedTest
    @MethodSource("validEvents")
    void acceptsEveryContractEventType(String type, String terminalFields) {
        RecoveryEventMessageHandler.Result result = handler.handle(event(type, terminalFields), RECEIVED_AT);

        assertThat(result).isEqualTo(RecoveryEventMessageHandler.Result.ACCEPTED);
        ArgumentCaptor<RecoveryEventReceived> captor = ArgumentCaptor.forClass(RecoveryEventReceived.class);
        verify(publisher).publishEvent(captor.capture());
        RecoveryEventReceived received = captor.getValue();
        assertThat(received.event().type().value()).isEqualTo(type);
        assertThat(received.event().eventId().toString()).isEqualTo("00000000-0000-0000-0000-000000000001");
        assertThat(received.receivedAt()).isEqualTo(RECEIVED_AT);
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("invalidEvents")
    void rejectsMalformedOrContractInvalidEvents(String name, byte[] payload) {
        assertThat(handler.handle(payload, RECEIVED_AT))
                .isEqualTo(RecoveryEventMessageHandler.Result.REJECTED);
        verify(publisher, never()).publishEvent(org.mockito.ArgumentMatchers.any());
    }

    @Test
    void rejectsOversizedPayloadAndStillAcceptsTheNextMessage() {
        byte[] oversized = new byte[RecoveryEventMessageHandler.MAX_PAYLOAD_BYTES + 1];

        assertThat(handler.handle(oversized, RECEIVED_AT))
                .isEqualTo(RecoveryEventMessageHandler.Result.REJECTED);
        verify(publisher, never()).publishEvent(org.mockito.ArgumentMatchers.any());

        reset(publisher);
        assertThat(handler.handle(event("recovery.started", ""), RECEIVED_AT))
                .isEqualTo(RecoveryEventMessageHandler.Result.ACCEPTED);
        verify(publisher).publishEvent(org.mockito.ArgumentMatchers.any(RecoveryEventReceived.class));
    }

    @ParameterizedTest
    @MethodSource("recoveryLevelEndEvents")
    void acceptsRecoveryLevelEndEventWithoutAttemptIdentity(String type, String terminalFields) {
        byte[] payload = replace(event(type, terminalFields),
                "\"recovery_attempt_id\":\"00000000-0000-0000-0000-000000000004\"",
                "\"recovery_attempt_id\":null");

        assertThat(handler.handle(payload, RECEIVED_AT))
                .isEqualTo(RecoveryEventMessageHandler.Result.ACCEPTED);
        verify(publisher).publishEvent(org.mockito.ArgumentMatchers.any(RecoveryEventReceived.class));
    }

    private static Stream<Arguments> validEvents() {
        return Stream.of(
                Arguments.of("recovery.started", ""),
                Arguments.of("recovery.completed",
                        ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1000,\"execution_id\":\"exec-new\""),
                Arguments.of("recovery.recovered",
                        ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1000"),
                Arguments.of("recovery.cancelled",
                        ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1000,\"error\":%s".formatted(error())),
                Arguments.of("recovery.failed",
                        ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1000,\"final\":true,\"error\":%s".formatted(error())),
                Arguments.of("recovery.failed",
                        ",\"attempt_duration_ms\":500,\"final\":false,\"next_retry_at\":\"2026-10-23T01:00:05Z\",\"error\":%s".formatted(error())));
    }

    private static Stream<Arguments> recoveryLevelEndEvents() {
        return Stream.of(
                Arguments.of("recovery.recovered", ",\"recovery_duration_ms\":1000"),
                Arguments.of("recovery.cancelled",
                        ",\"recovery_duration_ms\":1000,\"error\":%s".formatted(error())));
    }

    private static Stream<Arguments> invalidEvents() {
        return Stream.of(
                Arguments.of("malformed JSON", bytes("{not-json")),
                Arguments.of("missing required field", bytes("{\"type\":\"recovery.started\"}")),
                Arguments.of("invalid UUID", replace(event("recovery.started", ""),
                        "00000000-0000-0000-0000-000000000001", "not-a-uuid")),
                Arguments.of("invalid timestamp", replace(event("recovery.started", ""),
                        "2026-10-23T01:00:00Z", "yesterday")),
                Arguments.of("unknown event type", replace(event("recovery.started", ""),
                        "recovery.started", "recovery.unknown")),
                Arguments.of("attempt below one", replace(event("recovery.started", ""),
                        "\"attempt\":1", "\"attempt\":0")),
                Arguments.of("negative duration", event("recovery.completed",
                        ",\"attempt_duration_ms\":-1,\"recovery_duration_ms\":1000,\"execution_id\":\"exec-new\"")),
                Arguments.of("unknown field", appendField(event("recovery.started", ""), "\"unexpected\":true")),
                Arguments.of("started with terminal field", event("recovery.started", ",\"final\":true")),
                Arguments.of("completed without execution", event("recovery.completed",
                        ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1000")),
                Arguments.of("recovered with error", event("recovery.recovered",
                        ",\"recovery_duration_ms\":1000,\"error\":%s".formatted(error()))),
                Arguments.of("cancelled without error", event("recovery.cancelled",
                        ",\"recovery_duration_ms\":1000")),
                Arguments.of("failed without final", event("recovery.failed",
                        ",\"error\":%s".formatted(error()))),
                Arguments.of("retryable failed without next retry", event("recovery.failed",
                        ",\"final\":false,\"error\":%s".formatted(error()))),
                Arguments.of("final failed with next retry", event("recovery.failed",
                        ",\"recovery_duration_ms\":1000,\"final\":true,\"next_retry_at\":\"2026-10-23T01:00:05Z\",\"error\":%s".formatted(error()))),
                Arguments.of("failed without attempt id", replace(event("recovery.failed",
                        ",\"recovery_duration_ms\":1000,\"final\":true,\"error\":%s".formatted(error())),
                        "\"recovery_attempt_id\":\"00000000-0000-0000-0000-000000000004\"",
                        "\"recovery_attempt_id\":null")));
    }

    private static byte[] event(String type, String terminalFields) {
        return bytes("""
                {
                  "event_id":"00000000-0000-0000-0000-000000000001",
                  "type":"%s",
                  "vm_id":"00000000-0000-0000-0000-000000000002",
                  "recovery_id":"00000000-0000-0000-0000-000000000003",
                  "recovery_attempt_id":"00000000-0000-0000-0000-000000000004",
                  "attempt":1,
                  "reason":"HEARTBEAT_LOST",
                  "mode":"graceful-restart",
                  "detected_at":"2026-10-23T01:00:00Z",
                  "occurred_at":"2026-10-23T01:00:01Z"
                  %s
                }
                """.formatted(type, terminalFields));
    }

    private static String error() {
        return "{\"code\":\"XEN_EXECUTION_FAILED\",\"platform_code\":\"XEN_EXECUTION_FAILED\",\"message\":\"xl failed\"}";
    }

    private static byte[] replace(byte[] source, String before, String after) {
        return bytes(new String(source, StandardCharsets.UTF_8).replace(before, after));
    }

    private static byte[] appendField(byte[] source, String field) {
        String json = new String(source, StandardCharsets.UTF_8);
        return bytes(json.substring(0, json.lastIndexOf('}')) + "," + field + "}");
    }

    private static byte[] bytes(String value) {
        return value.getBytes(StandardCharsets.UTF_8);
    }
}
