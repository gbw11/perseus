package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.application.AgentMessageHandler.Result;
import com.xenova.backend.agent.domain.AgentCommandResult;
import com.xenova.backend.agent.domain.AgentCommandResultRepository;
import com.xenova.backend.agent.domain.AgentCommandStatus;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class CommandResultMessageHandlerIntegrationTest {

    private static final String VM_ID = "850e8400-e29b-41d4-a716-446655440001";
    private static final String COMMAND_ID = "command-1";
    private static final Instant RECEIVED_AT = Instant.parse("2026-11-07T06:00:00Z");

    private final Map<String, AgentCommandResult> rows = new LinkedHashMap<>();
    private final AgentCommandResultRepository repository = mock(AgentCommandResultRepository.class);
    private final AgentCommandResultStore store = new AgentCommandResultStore(repository);
    private final CommandResultMessageHandler handler = new CommandResultMessageHandler(new ObjectMapper(), store);

    @BeforeEach
    void cleanUp() {
        rows.clear();
        when(repository.findByVmIdAndCommandId(any(), any())).thenAnswer(call ->
                Optional.ofNullable(rows.get(call.getArgument(0) + ":" + call.getArgument(1))));
        when(repository.save(any())).thenAnswer(call -> {
            AgentCommandResult value = call.getArgument(0);
            rows.put(value.getVmId() + ":" + value.getCommandId(), value);
            return value;
        });
    }

    @Test
    void storesRunningThenTerminalResultAndIgnoresRedelivery() {
        store.recordPending(VM_ID, COMMAND_ID, "operation-1", "boot-1", RECEIVED_AT.minusSeconds(1));
        byte[] running = envelope("message-1", 1, "RUNNING", null, null);
        assertThat(handle(running)).isEqualTo(Result.ACCEPTED);
        assertThat(handle(running)).isEqualTo(Result.IGNORED);

        byte[] succeeded = envelope("message-2", 2, "SUCCEEDED", 30_000L, null);
        assertThat(handle(succeeded)).isEqualTo(Result.ACCEPTED);

        AgentCommandResult stored = stored();
        assertThat(stored.getStatus()).isEqualTo(AgentCommandStatus.SUCCEEDED);
        assertThat(stored.getDurationMs()).isEqualTo(30_000L);
        assertThat(stored.getSubmittedAt()).isEqualTo(RECEIVED_AT.minusSeconds(1));
        assertThat(stored.getResultJson()).contains("cpu_workers", "duration_s");
        assertThat(stored.isContractViolation()).isFalse();
    }

    @Test
    void preservesTerminalResultAndRecordsConflictingTerminalStatus() {
        assertThat(handle(envelope("message-1", 1, "RUNNING", null, null))).isEqualTo(Result.ACCEPTED);
        assertThat(handle(envelope("message-2", 2, "SUCCEEDED", 30_000L, null))).isEqualTo(Result.ACCEPTED);
        assertThat(handle(envelope("message-3", 3, "FAILED", 30_100L, "STRESS_FAILED")))
                .isEqualTo(Result.ACCEPTED);

        AgentCommandResult stored = stored();
        assertThat(stored.getStatus()).isEqualTo(AgentCommandStatus.SUCCEEDED);
        assertThat(stored.isContractViolation()).isTrue();
        assertThat(stored.getViolationDetail()).isEqualTo("invalid transition SUCCEEDED -> FAILED");
    }

    @Test
    void rejectsMalformedOrRetainedResults() {
        byte[] malformed = envelope("message-1", 1, "UNKNOWN", null, null);
        assertThat(handle(malformed)).isEqualTo(Result.REJECTED);
        assertThat(handler.handle(topic(), envelope("message-2", 2, "RUNNING", null, null), true, RECEIVED_AT))
                .isEqualTo(Result.REJECTED);
        assertThat(rows).isEmpty();
    }

    private Result handle(byte[] payload) {
        return handler.handle(topic(), payload, false, RECEIVED_AT);
    }

    private String topic() {
        return "xenova/v1/vms/" + VM_ID + "/command-result";
    }

    private AgentCommandResult stored() {
        return rows.get(VM_ID + ":" + COMMAND_ID);
    }

    private byte[] envelope(String messageId, long seq, String status, Long durationMs, String errorCode) {
        String error = errorCode == null ? "null"
                : "{\"code\":\"%s\",\"message\":\"failed\"}".formatted(errorCode);
        String finishedAt = switch (status) {
            case "SUCCEEDED", "FAILED" -> "\"2026-11-07T06:00:30Z\"";
            default -> "null";
        };
        String duration = durationMs == null ? "null" : durationMs.toString();
        return """
                {"schema_version":"1.0","message_id":"%s","vm_id":"%s","boot_id":"boot-1",
                 "agent_instance_id":"agent-1","type":"agent.command_result","seq":%d,"sent_at":null,
                 "payload":{"command_id":"%s","operation_id":"operation-1","status":"%s",
                 "started_at":"2026-11-07T06:00:00Z","finished_at":%s,"duration_ms":%s,
                 "result":{"cpu_workers":1,"memory_mib":0,"duration_s":30},"error":%s}}
                """.formatted(messageId, VM_ID, seq, COMMAND_ID, status, finishedAt, duration, error)
                .getBytes(StandardCharsets.UTF_8);
    }
}
