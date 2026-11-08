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
    private final AgentCommandResultStore commandResultStore;
    private final Supplier<AgentCommandPublisher> publisher;
    private final ObjectMapper objectMapper;
    private final Clock clock;

    @Autowired
    public AgentStressService(
            VmService vmService,
            AgentDataService agentDataService,
            AgentCommandResultStore commandResultStore,
            ObjectProvider<AgentCommandPublisher> publisher,
            ObjectMapper objectMapper,
            Clock clock) {
        this(vmService, agentDataService, commandResultStore,
                publisher::getIfAvailable, objectMapper, clock);
    }

    AgentStressService(
            VmService vmService,
            AgentDataService agentDataService,
            AgentCommandResultStore commandResultStore,
            Supplier<AgentCommandPublisher> publisher,
            ObjectMapper objectMapper,
            Clock clock) {
        this.vmService = vmService;
        this.agentDataService = agentDataService;
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
        if (guestKind != GuestKind.LINUX) {
            throw unsupportedGuestKind();
        }
        Instant now = clock.instant();
        String bootId = currentBootId(vmId, now);

        String commandId = UUID.randomUUID().toString();
        String operationId = UUID.randomUUID().toString();
        Instant expiresAt = now.plusSeconds(COMMAND_TTL_SECONDS);
        Map<String, Object> parameters = parameters(type, amount, durationS);

        Map<String, Object> command = new LinkedHashMap<>();
        command.put("command_id", commandId);
        command.put("operation_id", operationId);
        command.put("expected_boot_id", bootId);
        command.put("expires_at", timestamp(expiresAt));
        command.put("action", "stress");
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
        return new AgentStressCommand(
                commandId, operationId, vmId, type, amount, null, durationS, now, expiresAt);
    }

    private String currentBootId(String vmId, Instant now) {
        AgentHeartbeat heartbeat = agentDataService.findHeartbeat(vmId)
                .orElseThrow(() -> agentUnavailable(vmId));
        if (agentDataService.statusOf(heartbeat, now)
                != com.xenova.backend.agent.domain.AgentStatus.ONLINE) {
            throw agentUnavailable(vmId);
        }
        return requireBootId(vmId, heartbeat.getBootId());
    }

    private static String requireBootId(String vmId, String bootId) {
        if (bootId == null || bootId.isBlank()) {
            throw agentUnavailable(vmId);
        }
        return bootId;
    }

    private static Map<String, Object> parameters(
            AgentStressCommand.StressType type, int amount, int durationS) {
        Map<String, Object> parameters = new LinkedHashMap<>();
        parameters.put("cpu_workers", type == AgentStressCommand.StressType.CPU ? amount : 0);
        parameters.put("memory_mib", type == AgentStressCommand.StressType.MEMORY ? amount : 0);
        parameters.put("duration_s", durationS);
        return parameters;
    }

    private static ApiException unsupportedGuestKind() {
        return new ApiException(HttpStatus.BAD_REQUEST, "UNSUPPORTED_GUEST_KIND",
                "CPU·메모리 부하는 Linux VM에서만 지원합니다. Zephyr VM은 AUTO 연산 지연을 사용해야 합니다.");
    }

    private static ApiException agentUnavailable(String vmId) {
        return new ApiException(HttpStatus.CONFLICT, "AGENT_UNAVAILABLE",
                "VM Agent가 온라인 상태가 아닙니다: " + vmId);
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
