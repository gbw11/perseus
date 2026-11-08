package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.domain.AgentHeartbeat;
import com.xenova.backend.agent.domain.AgentStatus;
import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.application.VmService;
import com.xenova.backend.vm.domain.Vm;
import com.xenova.backend.vm.domain.VmState;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class AgentExitServiceTest {

    private static final String VM_ID = "850e8400-e29b-41d4-a716-446655440001";
    private static final Instant NOW = Instant.parse("2026-11-08T04:00:00Z");

    private final VmService vmService = mock(VmService.class);
    private final AgentDataService agentDataService = mock(AgentDataService.class);
    private final AgentCommandResultStore commandResultStore = mock(AgentCommandResultStore.class);
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final List<JsonNode> sent = new ArrayList<>();
    private AgentExitService service;

    @BeforeEach
    void setUp() {
        when(vmService.get(VM_ID)).thenReturn(vm(GuestKind.LINUX));
        AgentHeartbeat heartbeat = new AgentHeartbeat(VM_ID);
        heartbeat.update("boot-123", "agent-1", 3, NOW.minusSeconds(1), NOW.minusSeconds(1));
        when(agentDataService.findHeartbeat(VM_ID)).thenReturn(Optional.of(heartbeat));
        when(agentDataService.statusOf(any(), any())).thenReturn(AgentStatus.ONLINE);
        service = new AgentExitService(vmService, agentDataService, commandResultStore,
                () -> (topic, payload, qos) -> {
                    assertThat(topic).isEqualTo("xenova/v1/vms/" + VM_ID + "/command");
                    assertThat(qos).isEqualTo(1);
                    try {
                        sent.add(objectMapper.readTree(payload));
                    } catch (Exception exception) {
                        throw new IllegalStateException(exception);
                    }
                    return true;
                }, objectMapper, Clock.fixed(NOW, ZoneOffset.UTC));
    }

    @Test
    void publishesLinuxAgentExitCommandAndTracksResult() {
        AgentExitCommand result = service.send(VM_ID);

        JsonNode envelope = sent.get(0);
        JsonNode payload = envelope.path("payload");
        assertThat(envelope.path("type").asText()).isEqualTo("agent.command");
        assertThat(payload.path("command_id").asText()).isEqualTo(result.commandId());
        assertThat(payload.path("expected_boot_id").asText()).isEqualTo("boot-123");
        assertThat(payload.path("action").asText()).isEqualTo("agent_exit");
        assertThat(payload.path("parameters").isObject()).isTrue();
        assertThat(payload.path("parameters").isEmpty()).isTrue();
        verify(commandResultStore).recordPending(
                eq(VM_ID), eq(result.commandId()), eq(result.operationId()), eq("boot-123"), eq(NOW));
    }

    @Test
    void rejectsZephyrVm() {
        when(vmService.get(VM_ID)).thenReturn(vm(GuestKind.ZEPHYR));

        assertThatThrownBy(() -> service.send(VM_ID))
                .isInstanceOfSatisfying(ApiException.class,
                        exception -> assertThat(exception.getCode()).isEqualTo("UNSUPPORTED_GUEST_KIND"));
        assertThat(sent).isEmpty();
    }

    @Test
    void rejectsOfflineLinuxAgent() {
        when(agentDataService.statusOf(any(), any())).thenReturn(AgentStatus.OFFLINE);

        assertThatThrownBy(() -> service.send(VM_ID))
                .isInstanceOfSatisfying(ApiException.class,
                        exception -> assertThat(exception.getCode()).isEqualTo("AGENT_UNAVAILABLE"));
        assertThat(sent).isEmpty();
    }

    @Test
    void reportsUnavailableBrokerAndDiscardsPendingCommand() {
        service = new AgentExitService(vmService, agentDataService, commandResultStore,
                () -> null, objectMapper, Clock.fixed(NOW, ZoneOffset.UTC));

        assertThatThrownBy(() -> service.send(VM_ID))
                .isInstanceOfSatisfying(ApiException.class,
                        exception -> assertThat(exception.getCode()).isEqualTo("MQTT_UNAVAILABLE"));
        verify(commandResultStore).discardPending(eq(VM_ID), any());
    }

    private static Vm vm(GuestKind guestKind) {
        return new Vm(VM_ID, "exit-target", guestKind, VmState.RUNNING, "template-1", "1.0", 1, 512);
    }
}
