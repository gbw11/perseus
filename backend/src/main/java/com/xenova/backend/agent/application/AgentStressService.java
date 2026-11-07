package com.xenova.backend.agent.application;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.domain.AgentHeartbeat;
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
    private final Supplier<AgentCommandPublisher> publisher;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    @Autowired
    public AgentStressService(
            VmService vmService,
            AgentDataService agentDataService,
            ObjectProvider<AgentCommandPublisher> publisher,
            ObjectMapper objectMapper,
            Clock clock) {
        this(vmService, agentDataService, publisher::getIfAvailable, objectMapper, clock);
    }

    AgentStressService(
            VmService vmService,
            AgentDataService agentDataService,
            Supplier<AgentCommandPublisher> publisher,
            ObjectMapper objectMapper,
            Clock clock) {
        this.vmService = vmService;
        this.agentDataService = agentDataService;
        this.publisher = publisher;
        this.objectMapper = objectMapper;
        this.clock = clock;
    }

    public AgentStressCommand cpu(String vmId, int workers, int durationS) {
        return send(vmId, AgentStressCommand.StressType.CPU, workers, durationS);
    }

    public AgentStressCommand memory(String vmId, int memoryMib, int durationS) {
        return send(vmId, AgentStressCommand.StressType.MEMORY, memoryMib, durationS);
    }

    private AgentStressCommand send(String vmId, AgentStressCommand.StressType type, int amount, int durationS) {
        Vm vm = vmService.get(vmId);
        GuestKind guestKind = vm.guestKind();
        Instant now = clock.instant();
        AgentHeartbeat heartbeat = agentDataService.findHeartbeat(vmId).orElseThrow(() -> agentUnavailable(vmId));
        if (agentDataService.statusOf(heartbeat, now) != com.xenova.backend.agent.domain.AgentStatus.ONLINE
                || heartbeat.getBootId() == null || heartbeat.getBootId().isBlank()) {
            throw agentUnavailable(vmId);
        }

        String commandId = UUID.randomUUID().toString();
        String operationId = UUID.randomUUID().toString();
        Instant expiresAt = now.plusSeconds(COMMAND_TTL_SECONDS);
        Map<String, Object> parameters = new LinkedHashMap<>();
        parameters.put("cpu_workers", type == AgentStressCommand.StressType.CPU ? amount : 0);
        parameters.put("memory_mib", type == AgentStressCommand.StressType.MEMORY ? amount : 0);
        parameters.put("duration_s", durationS);

        Map<String, Object> command = new LinkedHashMap<>();
        command.put("command_id", commandId);
        command.put("operation_id", operationId);
        command.put("expected_boot_id", heartbeat.getBootId());
        command.put("expires_at", timestamp(expiresAt));
        command.put("action", stressAction(guestKind));
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
                    "MQTT 브로커에 연결되어 있지 않아 부하 명령을 보내지 못했습니다.");
        }
        return new AgentStressCommand(commandId, operationId, vmId, type, amount, durationS, now, expiresAt);
    }

    private static String stressAction(GuestKind guestKind) {
        return switch (guestKind) {
            case LINUX, ZEPHYR -> "stress";
        };
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

    private static ApiException agentUnavailable(String vmId) {
        return new ApiException(HttpStatus.CONFLICT, "AGENT_UNAVAILABLE",
                "VM Agent가 온라인 상태가 아닙니다: " + vmId);
    }
}
