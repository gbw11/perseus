package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.domain.AgentCommandResult;
import com.xenova.backend.agent.domain.AgentCommandResultReport;
import com.xenova.backend.agent.domain.AgentCommandStatus;
import com.xenova.backend.common.error.ApiException;
import java.time.Instant;
import java.util.Optional;
import org.junit.jupiter.api.Test;

class AgentCommandQueryServiceTest {

    private static final String VM_ID = "850e8400-e29b-41d4-a716-446655440001";
    private static final String COMMAND_ID = "command-1";
    private static final Instant NOW = Instant.parse("2026-11-07T06:00:00Z");

    private final AgentCommandResultStore store = mock(AgentCommandResultStore.class);
    private final AgentCommandQueryService service = new AgentCommandQueryService(store, new ObjectMapper());

    @Test
    void returnsPendingCommandBeforeMqttResultArrives() {
        AgentCommandResult pending = new AgentCommandResult(
                VM_ID, COMMAND_ID, "operation-1", "boot-1", NOW);
        when(store.find(VM_ID, COMMAND_ID)).thenReturn(Optional.of(pending));

        AgentCommandStatusResponse response = service.get(VM_ID, COMMAND_ID);

        assertThat(response.status()).isEqualTo(AgentCommandStatus.PENDING);
        assertThat(response.submittedAt()).isEqualTo(NOW);
        assertThat(response.result()).isNull();
        assertThat(response.error()).isNull();
    }

    @Test
    void returnsParsedTerminalResultAndError() {
        AgentCommandResult result = new AgentCommandResult(VM_ID, new AgentCommandResultReport(
                "message-1", "boot-1", "agent-1", 1, COMMAND_ID, "operation-1",
                AgentCommandStatus.FAILED, NOW, NOW.plusSeconds(10), 10_000L,
                "{\"workers\":1,\"load_percent\":80}",
                "{\"code\":\"STRESS_FAILED\",\"message\":\"failed\"}", NOW.plusSeconds(10)));
        when(store.find(VM_ID, COMMAND_ID)).thenReturn(Optional.of(result));

        AgentCommandStatusResponse response = service.get(VM_ID, COMMAND_ID);

        assertThat(response.status()).isEqualTo(AgentCommandStatus.FAILED);
        assertThat(response.result().path("load_percent").asInt()).isEqualTo(80);
        assertThat(response.error().path("code").asText()).isEqualTo("STRESS_FAILED");
    }

    @Test
    void reportsCommandNotFound() {
        when(store.find(VM_ID, COMMAND_ID)).thenReturn(Optional.empty());

        assertThatThrownBy(() -> service.get(VM_ID, COMMAND_ID))
                .isInstanceOfSatisfying(ApiException.class, exception -> {
                    assertThat(exception.getStatus().value()).isEqualTo(404);
                    assertThat(exception.getCode()).isEqualTo("COMMAND_NOT_FOUND");
                });
    }
}
