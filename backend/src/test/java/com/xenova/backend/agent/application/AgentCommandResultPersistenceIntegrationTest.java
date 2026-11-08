package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;

import com.xenova.backend.agent.domain.AgentCommandResultReport;
import com.xenova.backend.agent.domain.AgentCommandResultRepository;
import com.xenova.backend.agent.domain.AgentCommandStatus;
import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

@SpringBootTest
class AgentCommandResultPersistenceIntegrationTest {

    private static final String VM_ID = "850e8400-e29b-41d4-a716-446655440001";
    private static final String COMMAND_ID = "command-persistence-1";
    private static final Instant SUBMITTED_AT = Instant.parse("2026-11-07T06:00:00Z");

    @Autowired
    private AgentCommandResultStore store;

    @Autowired
    private AgentCommandQueryService queryService;

    @Autowired
    private AgentCommandResultRepository repository;

    @BeforeEach
    void cleanUp() {
        repository.deleteAll();
    }

    @Test
    void persistsPendingAndUpdatesItThroughTerminalResult() {
        store.recordPending(VM_ID, COMMAND_ID, "operation-1", "boot-1", SUBMITTED_AT);
        assertThat(queryService.get(VM_ID, COMMAND_ID).status()).isEqualTo(AgentCommandStatus.PENDING);

        store.record(VM_ID, report(AgentCommandStatus.RUNNING, null, null, 1));
        AgentCommandStatusResponse running = queryService.get(VM_ID, COMMAND_ID);
        assertThat(running.status()).isEqualTo(AgentCommandStatus.RUNNING);
        assertThat(running.submittedAt()).isEqualTo(SUBMITTED_AT);
        assertThat(running.result().path("load_percent").asInt()).isEqualTo(80);

        store.record(VM_ID, report(AgentCommandStatus.SUCCEEDED, 30_000L, null, 2));
        AgentCommandStatusResponse succeeded = queryService.get(VM_ID, COMMAND_ID);
        assertThat(succeeded.status()).isEqualTo(AgentCommandStatus.SUCCEEDED);
        assertThat(succeeded.durationMs()).isEqualTo(30_000L);
        assertThat(succeeded.contractViolation()).isFalse();
    }

    @Test
    void removesOnlyPendingCommandAfterPublishFailure() {
        store.recordPending(VM_ID, COMMAND_ID, "operation-1", "boot-1", SUBMITTED_AT);

        store.discardPending(VM_ID, COMMAND_ID);

        assertThat(store.find(VM_ID, COMMAND_ID)).isEmpty();
    }

    private AgentCommandResultReport report(
            AgentCommandStatus status, Long durationMs, String errorJson, long seq) {
        Instant finishedAt = status.terminal() ? SUBMITTED_AT.plusSeconds(30) : null;
        return new AgentCommandResultReport(
                "message-" + seq, "boot-1", "agent-1", seq, COMMAND_ID, "operation-1", status,
                SUBMITTED_AT.plusSeconds(1), finishedAt, durationMs,
                "{\"workers\":1,\"load_percent\":80,\"duration_s\":30}", errorJson,
                SUBMITTED_AT.plusSeconds(seq));
    }
}
