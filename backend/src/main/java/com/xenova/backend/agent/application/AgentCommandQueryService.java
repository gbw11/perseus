package com.xenova.backend.agent.application;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.domain.AgentCommandResult;
import com.xenova.backend.common.error.ApiException;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

@Service
public class AgentCommandQueryService {

    private final AgentCommandResultStore store;
    private final ObjectMapper objectMapper;

    public AgentCommandQueryService(AgentCommandResultStore store, ObjectMapper objectMapper) {
        this.store = store;
        this.objectMapper = objectMapper;
    }

    public AgentCommandStatusResponse get(String vmId, String commandId) {
        AgentCommandResult result = store.find(vmId, commandId).orElseThrow(() ->
                new ApiException(HttpStatus.NOT_FOUND, "COMMAND_NOT_FOUND", "명령을 찾을 수 없습니다."));
        return new AgentCommandStatusResponse(
                result.getVmId(), result.getCommandId(), result.getOperationId(), result.getStatus(),
                result.getSubmittedAt(), result.getStartedAt(), result.getFinishedAt(), result.getDurationMs(),
                read(result.getResultJson()), read(result.getErrorJson()), result.getReceivedAt(),
                result.isContractViolation(), result.getViolationDetail());
    }

    private JsonNode read(String json) {
        if (json == null) {
            return null;
        }
        try {
            return objectMapper.readTree(json);
        } catch (JsonProcessingException exception) {
            throw new IllegalStateException("저장된 명령 결과 JSON을 읽을 수 없습니다.", exception);
        }
    }
}
