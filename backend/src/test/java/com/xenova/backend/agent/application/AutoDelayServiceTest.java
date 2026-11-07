package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.domain.TelemetryHeartbeat;
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
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class AutoDelayServiceTest {

    private static final String VM_ID = "850e8400-e29b-41d4-a716-446655440001";
    private static final Instant NOW = Instant.parse("2026-11-07T05:00:00Z");

    private final VmService vmService = mock(VmService.class);
    private final TelemetryHeartbeatStore telemetryHeartbeatStore = mock(TelemetryHeartbeatStore.class);
    private final ObjectMapper objectMapper = new ObjectMapper();
    private final List<JsonNode> sent = new ArrayList<>();
    private AutoDelayService service;

    @BeforeEach
    void setUp() {
        when(vmService.get(VM_ID)).thenReturn(vm(GuestKind.ZEPHYR));
        when(telemetryHeartbeatStore.view(VM_ID)).thenReturn(freshHeartbeat());
        service = new AutoDelayService(vmService, telemetryHeartbeatStore, () -> (topic, payload, qos) -> {
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
    void publishesZephyrAutoDelayCommand() {
        AutoDelayCommand result = service.send(VM_ID, 50, 30);

        JsonNode envelope = sent.get(0);
        JsonNode payload = envelope.path("payload");
        assertThat(envelope.path("type").asText()).isEqualTo("agent.command");
        assertThat(payload.path("command_id").asText()).isEqualTo(result.commandId());
        assertThat(payload.path("expected_boot_id").asText()).isEqualTo("zephyr-boot");
        assertThat(payload.path("action").asText()).isEqualTo("auto_delay");
        assertThat(payload.path("parameters").fieldNames())
                .toIterable().containsExactly("delay_ms", "duration_s");
        assertThat(payload.path("parameters").path("delay_ms").asInt()).isEqualTo(50);
        assertThat(payload.path("parameters").path("duration_s").asInt()).isEqualTo(30);
        assertThat(result.delayMs()).isEqualTo(50);
    }

    @Test
    void rejectsLinuxVm() {
        when(vmService.get(VM_ID)).thenReturn(vm(GuestKind.LINUX));

        assertThatThrownBy(() -> service.send(VM_ID, 50, 30))
                .isInstanceOfSatisfying(ApiException.class,
                        exception -> assertThat(exception.getCode()).isEqualTo("UNSUPPORTED_GUEST_KIND"));
        assertThat(sent).isEmpty();
    }

    @Test
    void rejectsStaleZephyrTelemetry() {
        when(telemetryHeartbeatStore.view(VM_ID)).thenReturn(
                new TelemetryHeartbeatStore.View(TelemetryHeartbeatStore.Status.STALE, null, 7_000L));

        assertThatThrownBy(() -> service.send(VM_ID, 50, 30))
                .isInstanceOfSatisfying(ApiException.class,
                        exception -> assertThat(exception.getCode()).isEqualTo("AGENT_UNAVAILABLE"));
        assertThat(sent).isEmpty();
    }

    @Test
    void reportsUnavailableBroker() {
        service = new AutoDelayService(vmService, telemetryHeartbeatStore, () -> null, objectMapper,
                Clock.fixed(NOW, ZoneOffset.UTC));

        assertThatThrownBy(() -> service.send(VM_ID, 50, 30))
                .isInstanceOfSatisfying(ApiException.class,
                        exception -> assertThat(exception.getCode()).isEqualTo("MQTT_UNAVAILABLE"));
    }

    private static Vm vm(GuestKind guestKind) {
        return new Vm(VM_ID, "delay-target", guestKind, VmState.RUNNING, "template-1", "1.0", 1, 16);
    }

    private static TelemetryHeartbeatStore.View freshHeartbeat() {
        TelemetryHeartbeat.Report heartbeat = new TelemetryHeartbeat.Report(
                "zephyr-boot", "publisher-1", "1", "1000", "running", "1000", "0", "0", "1", NOW);
        return new TelemetryHeartbeatStore.View(TelemetryHeartbeatStore.Status.FRESH, heartbeat, 0L);
    }
}
