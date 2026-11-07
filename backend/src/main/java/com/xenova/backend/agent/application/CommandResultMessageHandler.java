package com.xenova.backend.agent.application;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.domain.AgentCommandResult.Observation;
import com.xenova.backend.agent.domain.AgentCommandResultReport;
import com.xenova.backend.agent.domain.AgentCommandStatus;
import java.io.IOException;
import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

@Component
public class CommandResultMessageHandler {

    public static final String TOPIC = "xenova/v1/vms/+/command-result";

    private static final int MAX_PAYLOAD_BYTES = 64 * 1024;
    private static final Pattern TOPIC_PATTERN = Pattern.compile("^xenova/v1/vms/([^/]+)/command-result$");
    private static final Set<String> STATUSES = Set.of("ACCEPTED", "RUNNING", "SUCCEEDED", "FAILED", "REJECTED");
    private static final Logger log = LoggerFactory.getLogger(CommandResultMessageHandler.class);

    private final ObjectMapper objectMapper;
    private final AgentCommandResultStore store;

    public CommandResultMessageHandler(ObjectMapper objectMapper, AgentCommandResultStore store) {
        this.objectMapper = objectMapper;
        this.store = store;
    }

    public static boolean matches(String topic) {
        return TOPIC_PATTERN.matcher(topic).matches();
    }

    public AgentMessageHandler.Result handle(String topic, byte[] payload, boolean retained, Instant receivedAt) {
        try {
            return process(topic, payload, retained, receivedAt);
        } catch (Rejected rejected) {
            log.warn("Rejected command result on {}: {}", topic, rejected.getMessage());
            return AgentMessageHandler.Result.REJECTED;
        } catch (RuntimeException exception) {
            log.error("Failed to store command result on {}", topic, exception);
            return AgentMessageHandler.Result.REJECTED;
        }
    }

    private AgentMessageHandler.Result process(String topic, byte[] bytes, boolean retained, Instant receivedAt) {
        Matcher matcher = TOPIC_PATTERN.matcher(topic);
        if (!matcher.matches() || retained || bytes.length > MAX_PAYLOAD_BYTES) {
            throw new Rejected("invalid topic, retained flag, or payload size");
        }
        String vmId = matcher.group(1);
        JsonNode root = read(bytes);
        if (!"1.0".equals(text(root, "schema_version"))
                || !vmId.equals(text(root, "vm_id"))
                || !"agent.command_result".equals(text(root, "type"))
                || blank(text(root, "message_id"))
                || blank(text(root, "boot_id"))
                || blank(text(root, "agent_instance_id"))) {
            throw new Rejected("invalid command-result envelope");
        }
        JsonNode seqNode = root.path("seq");
        if (!seqNode.isIntegralNumber() || !seqNode.canConvertToLong() || seqNode.longValue() < 1) {
            throw new Rejected("invalid sequence");
        }

        JsonNode body = root.path("payload");
        if (!body.isObject() || !hasAllResultFields(body)) {
            throw new Rejected("missing command-result payload field");
        }
        String commandId = text(body, "command_id");
        String operationId = nullableText(body.get("operation_id"), 128);
        String statusValue = text(body, "status");
        if (blank(commandId) || commandId.length() > 128 || !STATUSES.contains(statusValue)) {
            throw new Rejected("invalid command identity or status");
        }
        AgentCommandStatus status = AgentCommandStatus.valueOf(statusValue);
        Instant startedAt = nullableInstant(body.get("started_at"));
        Instant finishedAt = nullableInstant(body.get("finished_at"));
        Long durationMs = nullableDuration(body.get("duration_ms"));
        JsonNode result = nullableObject(body.get("result"));
        JsonNode error = nullableObject(body.get("error"));
        validateStatus(status, durationMs, result, error);

        AgentCommandResultReport report = new AgentCommandResultReport(
                text(root, "message_id"), text(root, "boot_id"), text(root, "agent_instance_id"),
                seqNode.longValue(), commandId, operationId, status, startedAt, finishedAt, durationMs,
                json(result), json(error), receivedAt);
        Observation observation = store.record(vmId, report);
        if (observation == Observation.CONTRACT_VIOLATION) {
            log.warn("Command result contract violation for VM {} command {}", vmId, commandId);
        }
        return observation == Observation.DUPLICATE
                ? AgentMessageHandler.Result.IGNORED : AgentMessageHandler.Result.ACCEPTED;
    }

    private static boolean hasAllResultFields(JsonNode body) {
        return body.has("command_id") && body.has("operation_id") && body.has("status")
                && body.has("started_at") && body.has("finished_at") && body.has("duration_ms")
                && body.has("result") && body.has("error");
    }

    private static void validateStatus(
            AgentCommandStatus status, Long durationMs, JsonNode result, JsonNode error) {
        boolean valid = switch (status) {
            case ACCEPTED -> durationMs == null && error == null;
            case RUNNING -> durationMs == null && result != null && error == null;
            case SUCCEEDED -> durationMs != null && result != null && error == null;
            case FAILED -> result != null && validError(error);
            case REJECTED -> durationMs == null && validError(error);
        };
        if (!valid) {
            throw new Rejected("fields do not match status " + status);
        }
    }

    private static boolean validError(JsonNode error) {
        return error != null && !blank(text(error, "code")) && !blank(text(error, "message"));
    }

    private JsonNode read(byte[] payload) {
        try {
            JsonNode root = objectMapper.readTree(payload);
            if (root == null || !root.isObject()) {
                throw new Rejected("not a JSON object");
            }
            return root;
        } catch (IOException exception) {
            throw new Rejected("malformed JSON");
        }
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        return value != null && value.isTextual() ? value.textValue() : null;
    }

    private static String nullableText(JsonNode value, int maxLength) {
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual() || value.textValue().length() > maxLength) {
            throw new Rejected("invalid optional string");
        }
        return value.textValue();
    }

    private static Instant nullableInstant(JsonNode value) {
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isTextual()) {
            throw new Rejected("invalid timestamp");
        }
        try {
            return Instant.parse(value.textValue());
        } catch (DateTimeParseException exception) {
            throw new Rejected("invalid timestamp");
        }
    }

    private static Long nullableDuration(JsonNode value) {
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isIntegralNumber() || !value.canConvertToLong() || value.longValue() < 0) {
            throw new Rejected("invalid duration_ms");
        }
        return value.longValue();
    }

    private static JsonNode nullableObject(JsonNode value) {
        if (value == null || value.isNull()) {
            return null;
        }
        if (!value.isObject()) {
            throw new Rejected("result and error must be objects or null");
        }
        return value;
    }

    private static String json(JsonNode value) {
        return value == null ? null : value.toString();
    }

    private static boolean blank(String value) {
        return value == null || value.isBlank();
    }

    private static final class Rejected extends RuntimeException {

        Rejected(String message) {
            super(message);
        }
    }
}
