package com.xenova.backend.agent.application;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.domain.TelemetryHeartbeat;
import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.application.VmService;
import com.xenova.backend.vm.domain.Vm;
import java.time.Clock;
import java.time.Instant;
import java.time.format.DateTimeFormatter;
import java.time.temporal.ChronoUnit;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import java.util.function.Supplier;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

@Service
public class AutoDelayService {

    private static final int COMMAND_TTL_SECONDS = 30;
    private static final String SCHEMA_VERSION = "1.0";

    private final VmService vmService;
    private final TelemetryHeartbeatStore telemetryHeartbeatStore;
    private final Supplier<AgentCommandPublisher> publisher;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    @Autowired
    public AutoDelayService(
            VmService vmService,
            TelemetryHeartbeatStore telemetryHeartbeatStore,
            ObjectProvider<AgentCommandPublisher> publisher,
            ObjectMapper objectMapper,
            Clock clock) {
        this(vmService, telemetryHeartbeatStore, publisher::getIfAvailable, objectMapper, clock);
    }

    AutoDelayService(
            VmService vmService,
            TelemetryHeartbeatStore telemetryHeartbeatStore,
            Supplier<AgentCommandPublisher> publisher,
            ObjectMapper objectMapper,
            Clock clock) {
        this.vmService = vmService;
        this.telemetryHeartbeatStore = telemetryHeartbeatStore;
        this.publisher = publisher;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    public AutoDelayCommand send(String vmId, int delayMs, int durationS) {
        Vm vm = vmService.get(vmId);
        if (vm.guestKind() != GuestKind.ZEPHYR) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "UNSUPPORTED_GUEST_KIND",
                    "auto_delay는 Zephyr VM에서만 지원합니다.");
        }

        TelemetryHeartbeatStore.View view = telemetryHeartbeatStore.view(vmId);
        TelemetryHeartbeat.Report heartbeat = view.heartbeat();
        if (view.status() != TelemetryHeartbeatStore.Status.FRESH || heartbeat == null
                || heartbeat.bootId() == null || heartbeat.bootId().isBlank()) {
            throw new ApiException(HttpStatus.CONFLICT, "AGENT_UNAVAILABLE",
                    "Zephyr telemetry publisher가 온라인 상태가 아닙니다: " + vmId);
        }

        Instant now = clock.instant();
        Instant expiresAt = now.plusSeconds(COMMAND_TTL_SECONDS);
        String commandId = UUID.randomUUID().toString();
        String operationId = UUID.randomUUID().toString();

        Map<String, Object> parameters = new LinkedHashMap<>();
        parameters.put("delay_ms", delayMs);
        parameters.put("duration_s", durationS);

        Map<String, Object> command = new LinkedHashMap<>();
        command.put("command_id", commandId);
        command.put("operation_id", operationId);
        command.put("expected_boot_id", heartbeat.bootId());
        command.put("expires_at", timestamp(expiresAt));
        command.put("action", "auto_delay");
        command.put("parameters", parameters);

        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schema_version", SCHEMA_VERSION);
        envelope.put("message_id", UUID.randomUUID().toString());
        envelope.put("vm_id", vmId);
        envelope.put("type", "agent.command");
        envelope.put("sent_at", timestamp(now));
        envelope.put("payload", command);

        AgentCommandPublisher target = publisher.get();
        if (target == null || !target.publish("xenova/v1/vms/" + vmId + "/command", serialize(envelope), 1)) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "MQTT_UNAVAILABLE",
                    "MQTT 브로커에 연결되어 있지 않아 연산 지연 명령을 보내지 못했습니다.");
        }
        return new AutoDelayCommand(commandId, operationId, vmId, delayMs, durationS, now, expiresAt);
    }

    private byte[] serialize(Map<String, Object> envelope) {
        try {
            return objectMapper.writeValueAsBytes(envelope);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException(exception);
        }
    }

    private static String timestamp(Instant instant) {
        return DateTimeFormatter.ISO_INSTANT.format(instant.truncatedTo(ChronoUnit.MILLIS));
    }
}
