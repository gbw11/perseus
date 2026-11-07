package com.xenova.backend.agent.application;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.domain.AgentHeartbeat;
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
public class AgentStressService {

    private static final int COMMAND_TTL_SECONDS = 30;
    private static final String SCHEMA_VERSION = "1.0";

    private final VmService vmService;
    private final AgentDataService agentDataService;
    private final TelemetryHeartbeatStore telemetryHeartbeatStore;
    private final AgentCommandResultStore commandResultStore;
    private final Supplier<AgentCommandPublisher> publisher;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    @Autowired
    public AgentStressService(
            VmService vmService,
            AgentDataService agentDataService,
            TelemetryHeartbeatStore telemetryHeartbeatStore,
            AgentCommandResultStore commandResultStore,
            ObjectProvider<AgentCommandPublisher> publisher,
            ObjectMapper objectMapper,
            Clock clock) {
        this(vmService, agentDataService, telemetryHeartbeatStore, commandResultStore,
                publisher::getIfAvailable, objectMapper, clock);
    }

    AgentStressService(
            VmService vmService,
            AgentDataService agentDataService,
            TelemetryHeartbeatStore telemetryHeartbeatStore,
            AgentCommandResultStore commandResultStore,
            Supplier<AgentCommandPublisher> publisher,
            ObjectMapper objectMapper,
            Clock clock) {
        this.vmService = vmService;
        this.agentDataService = agentDataService;
        this.telemetryHeartbeatStore = telemetryHeartbeatStore;
        this.commandResultStore = commandResultStore;
        this.publisher = publisher;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    public AgentStressCommand cpu(String vmId, int workers, Integer loadPercent, int durationS) {
        return send(vmId, AgentStressCommand.StressType.CPU, workers, loadPercent, durationS);
    }

    public AgentStressCommand memory(String vmId, int memoryMib, int durationS) {
        return send(vmId, AgentStressCommand.StressType.MEMORY, memoryMib, null, durationS);
    }

    private AgentStressCommand send(
            String vmId, AgentStressCommand.StressType type, int amount, Integer loadPercent, int durationS) {
        Vm vm = vmService.get(vmId);
        GuestKind guestKind = vm.guestKind();
        Instant now = clock.instant();
        String bootId = currentBootId(vmId, guestKind, now);

        String commandId = UUID.randomUUID().toString();
        String operationId = UUID.randomUUID().toString();
        Instant expiresAt = now.plusSeconds(COMMAND_TTL_SECONDS);
        Map<String, Object> parameters = parameters(guestKind, type, amount, loadPercent, durationS);

        Map<String, Object> command = new LinkedHashMap<>();
        command.put("command_id", commandId);
        command.put("operation_id", operationId);
        command.put("expected_boot_id", bootId);
        command.put("expires_at", timestamp(expiresAt));
        command.put("action", action(guestKind, type));
        command.put("parameters", parameters);

        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("schema_version", SCHEMA_VERSION);
        envelope.put("message_id", UUID.randomUUID().toString());
        envelope.put("vm_id", vmId);
        envelope.put("type", "agent.command");
        envelope.put("sent_at", timestamp(now));
        envelope.put("payload", command);

        byte[] message = serialize(envelope);
        commandResultStore.recordPending(vmId, commandId, operationId, bootId, now);
        AgentCommandPublisher target = publisher.get();
        if (target == null || !target.publish("xenova/v1/vms/" + vmId + "/command", message, 1)) {
            commandResultStore.discardPending(vmId, commandId);
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "MQTT_UNAVAILABLE",
                    "MQTT 브로커에 연결되어 있지 않아 부하 명령을 보내지 못했습니다.");
        }
        Integer appliedLoadPercent = guestKind == GuestKind.ZEPHYR
                && type == AgentStressCommand.StressType.CPU ? loadPercent : null;
        return new AgentStressCommand(
                commandId, operationId, vmId, type, amount, appliedLoadPercent, durationS, now, expiresAt);
    }

    private String currentBootId(String vmId, GuestKind guestKind, Instant now) {
        return switch (guestKind) {
            case LINUX -> {
                AgentHeartbeat heartbeat = agentDataService.findHeartbeat(vmId)
                        .orElseThrow(() -> agentUnavailable(vmId));
                if (agentDataService.statusOf(heartbeat, now)
                        != com.xenova.backend.agent.domain.AgentStatus.ONLINE) {
                    throw agentUnavailable(vmId);
                }
                yield requireBootId(vmId, heartbeat.getBootId());
            }
            case ZEPHYR -> {
                TelemetryHeartbeatStore.View view = telemetryHeartbeatStore.view(vmId);
                TelemetryHeartbeat.Report heartbeat = view.heartbeat();
                if (view.status() != TelemetryHeartbeatStore.Status.FRESH || heartbeat == null) {
                    throw agentUnavailable(vmId);
                }
                yield requireBootId(vmId, heartbeat.bootId());
            }
        };
    }

    private static String requireBootId(String vmId, String bootId) {
        if (bootId == null || bootId.isBlank()) {
            throw agentUnavailable(vmId);
        }
        return bootId;
    }

    private static String action(GuestKind guestKind, AgentStressCommand.StressType type) {
        return switch (guestKind) {
            case LINUX -> "stress";
            case ZEPHYR -> type == AgentStressCommand.StressType.CPU ? "cpu_stress" : "memory_stress";
        };
    }

    private static Map<String, Object> parameters(
            GuestKind guestKind, AgentStressCommand.StressType type, int amount, Integer loadPercent, int durationS) {
        Map<String, Object> parameters = new LinkedHashMap<>();
        if (guestKind == GuestKind.LINUX) {
            parameters.put("cpu_workers", type == AgentStressCommand.StressType.CPU ? amount : 0);
            parameters.put("memory_mib", type == AgentStressCommand.StressType.MEMORY ? amount : 0);
        } else if (type == AgentStressCommand.StressType.CPU) {
            if (amount != 1 || loadPercent == null) {
                throw invalidParameters("Zephyr CPU 부하는 workers=1과 load_percent가 필요합니다.");
            }
            parameters.put("workers", amount);
            parameters.put("load_percent", loadPercent);
        } else {
            if (amount > 8) {
                throw invalidParameters("Zephyr 메모리 부하는 1~8 MiB만 지원합니다.");
            }
            parameters.put("memory_mib", amount);
        }
        parameters.put("duration_s", durationS);
        return parameters;
    }

    private static ApiException invalidParameters(String message) {
        return new ApiException(HttpStatus.BAD_REQUEST, "INVALID_STRESS_PARAMETERS", message);
    }

    private static ApiException agentUnavailable(String vmId) {
        return new ApiException(HttpStatus.CONFLICT, "AGENT_UNAVAILABLE",
                "VM Agent 또는 Zephyr telemetry publisher가 온라인 상태가 아닙니다: " + vmId);
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
