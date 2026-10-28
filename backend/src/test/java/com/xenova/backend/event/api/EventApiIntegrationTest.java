package com.xenova.backend.event.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.application.AgentDataService;
import com.xenova.backend.event.application.AgentStatusWatcher;
import com.xenova.backend.event.application.RecoveryEventMessageHandler;
import com.xenova.backend.event.domain.ReportedAgentStatusRepository;
import com.xenova.backend.event.domain.VmEventRepository;
import com.xenova.backend.event.domain.VmRecoveryHealthRepository;
import com.xenova.backend.vm.domain.VmControlPort;
import com.xenova.backend.vm.domain.VmLaunchSpec;
import com.xenova.backend.vm.domain.VmRecordRepository;
import com.xenova.backend.vm.domain.VmRuntime;
import com.xenova.backend.vm.domain.VmState;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

@SpringBootTest
@AutoConfigureMockMvc
class EventApiIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"event-api-user","nickname":"이벤트 API 테스트","password":"password123!"}
            """;

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @MockitoBean
    private VmControlPort vmControlPort;

    @Autowired
    private VmRecordRepository vmRecordRepository;

    @Autowired
    private VmEventRepository vmEventRepository;

    @Autowired
    private VmRecoveryHealthRepository vmRecoveryHealthRepository;

    @Autowired
    private ReportedAgentStatusRepository reportedAgentStatusRepository;

    @Autowired
    private AgentDataService agentDataService;

    @Autowired
    private AgentStatusWatcher agentStatusWatcher;

    @Autowired
    private RecoveryEventMessageHandler recoveryEventMessageHandler;

    private String accessToken;

    @BeforeEach
    void setUp() throws Exception {
        vmRecoveryHealthRepository.deleteAll();
        vmEventRepository.deleteAll();
        reportedAgentStatusRepository.deleteAll();
        vmRecordRepository.deleteAll();
        when(vmControlPort.launch(anyString(), any(VmLaunchSpec.class))).thenAnswer(invocation -> {
            VmLaunchSpec spec = invocation.getArgument(1);
            return new VmRuntime(invocation.getArgument(0), VmState.RUNNING, spec.vcpus(), spec.memoryMib());
        });
        mockMvc.perform(post("/api/v1/auth/signup")
                .contentType(MediaType.APPLICATION_JSON)
                .content(CREDENTIALS));
        String body = mockMvc.perform(post("/api/v1/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(CREDENTIALS))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        accessToken = objectMapper.readTree(body).get("access_token").asText();
    }

    @Test
    void vmLifecycleIsRecordedNewestFirst() throws Exception {
        String vmId = createVm("event-web-01");
        // Running before the stop, absent afterwards so the start launches it again.
        when(vmControlPort.find(vmId))
                .thenReturn(Optional.of(new VmRuntime(vmId, VmState.RUNNING, 1, 512)))
                .thenReturn(Optional.empty());
        mockMvc.perform(authorized(post("/api/v1/vms/" + vmId + "/stop"))).andExpect(status().isOk());
        mockMvc.perform(authorized(post("/api/v1/vms/" + vmId + "/start"))).andExpect(status().isOk());
        mockMvc.perform(authorized(delete("/api/v1/vms/" + vmId))).andExpect(status().isNoContent());

        JsonNode items = events("?vm_id=" + vmId);

        assertThat(types(items)).containsExactly(
                "vm.deleted", "vm.state.changed", "vm.state.changed", "vm.created");
        JsonNode started = items.get(1);
        assertThat(started.path("severity").asText()).isEqualTo("INFO");
        assertThat(started.path("vm_id").asText()).isEqualTo(vmId);
        assertThat(started.path("payload").path("from").asText()).isEqualTo("stopped");
        assertThat(started.path("payload").path("to").asText()).isEqualTo("running");
        assertThat(started.path("payload").path("cause").asText()).isEqualTo("user_start");
        assertThat(items.get(2).path("payload").path("cause").asText()).isEqualTo("user_stop");
        assertThat(items.get(2).path("payload").path("from").asText()).isEqualTo("running");
        assertThat(items.get(3).path("payload").path("name").asText()).isEqualTo("event-web-01");
        assertThat(items.get(3).path("payload").path("template_id").asText()).isEqualTo("debian-13-arm64");
        assertThat(items.get(0).path("payload").path("name").asText()).isEqualTo("event-web-01");
        assertThat(items.get(0).has("event_id")).isTrue();
        assertThat(items.get(0).has("recorded_at")).isTrue();
    }

    @Test
    void afterEventIdReturnsLaterEventsOldestFirst() throws Exception {
        String vmId = createVm("event-web-02");
        // Running before the stop, absent afterwards so the start launches it again.
        when(vmControlPort.find(vmId))
                .thenReturn(Optional.of(new VmRuntime(vmId, VmState.RUNNING, 1, 512)))
                .thenReturn(Optional.empty());
        mockMvc.perform(authorized(post("/api/v1/vms/" + vmId + "/stop"))).andExpect(status().isOk());
        mockMvc.perform(authorized(post("/api/v1/vms/" + vmId + "/start"))).andExpect(status().isOk());
        String createdEventId = events("?type=vm.created").get(0).path("event_id").asText();

        JsonNode items = events("?after_event_id=" + createdEventId);

        assertThat(types(items)).containsExactly("vm.state.changed", "vm.state.changed");
        assertThat(items.get(0).path("payload").path("to").asText()).isEqualTo("stopped");
        assertThat(items.get(1).path("payload").path("to").asText()).isEqualTo("running");
    }

    @Test
    void filtersAndLimitNarrowTheResult() throws Exception {
        createVm("event-web-03");
        createVm("event-web-04");

        assertThat(events("?limit=1")).hasSize(1);
        assertThat(events("?type=vm.created&severity=INFO")).hasSize(2);
        assertThat(events("?severity=ERROR")).isEmpty();
        assertThat(events("?from=2000-01-01T00:00:00Z&to=2000-01-02T00:00:00Z")).isEmpty();
    }

    @Test
    void recoveryEventsExposeTheMqttContractThroughTheRestApi() throws Exception {
        acceptRecoveryEvent(601, "recovery.started", "");
        acceptRecoveryEvent(602, "recovery.completed",
                ",\"attempt_duration_ms\":500,\"recovery_duration_ms\":1000,\"execution_id\":\"exec-new\"");
        acceptRecoveryEvent(603, "recovery.recovered",
                ",\"attempt_duration_ms\":510,\"recovery_duration_ms\":1010");
        acceptRecoveryEvent(604, "recovery.cancelled",
                ",\"attempt_duration_ms\":520,\"recovery_duration_ms\":1020,\"error\":" + recoveryError());
        acceptRecoveryEvent(605, "recovery.failed",
                ",\"attempt_duration_ms\":530,\"final\":false,"
                        + "\"next_retry_at\":\"2026-10-28T00:00:05Z\",\"error\":" + recoveryError());
        acceptRecoveryEvent(606, "recovery.failed",
                ",\"attempt_duration_ms\":540,\"recovery_duration_ms\":1040,"
                        + "\"final\":true,\"error\":" + recoveryError());

        JsonNode items = events("?vm_id=" + RECOVERY_VM_ID);

        assertRecoveryEvent(items, 601, "recovery.started", "INFO");
        JsonNode completed = assertRecoveryEvent(items, 602, "recovery.completed", "INFO");
        assertThat(completed.path("payload").path("recovery_duration_ms").asLong()).isEqualTo(1000L);
        assertThat(completed.path("payload").path("execution_id").asText()).isEqualTo("exec-new");
        assertRecoveryEvent(items, 603, "recovery.recovered", "INFO");
        JsonNode cancelled = assertRecoveryEvent(items, 604, "recovery.cancelled", "INFO");
        assertThat(cancelled.path("payload").path("error").path("code").asText())
                .isEqualTo("XEN_EXECUTION_FAILED");
        JsonNode retryableFailure = assertRecoveryEvent(items, 605, "recovery.failed", "WARNING");
        assertThat(retryableFailure.path("payload").path("final").asBoolean()).isFalse();
        assertThat(retryableFailure.path("payload").path("next_retry_at").asText())
                .isEqualTo("2026-10-28T00:00:05Z");
        JsonNode finalFailure = assertRecoveryEvent(items, 606, "recovery.failed", "ERROR");
        assertThat(finalFailure.path("payload").path("final").asBoolean()).isTrue();
        assertThat(finalFailure.path("payload").path("recovery_duration_ms").asLong()).isEqualTo(1040L);

        assertThat(events("?vm_id=" + RECOVERY_VM_ID + "&type=recovery.completed")).hasSize(1);
    }

    @Test
    void agentStatusChangesAreRecordedOncePerTransition() throws Exception {
        String vmId = createVm("event-web-05");
        Instant now = Instant.now();
        agentDataService.recordHeartbeat(vmId, "boot-1", "agent-1", 1, now, now);
        agentStatusWatcher.check();

        // An old heartbeat makes the agent OFFLINE without waiting in the test.
        Instant old = now.minusSeconds(60);
        agentDataService.recordHeartbeat(vmId, "boot-1", "agent-1", 2, old, old);
        agentStatusWatcher.check();
        agentStatusWatcher.check();

        JsonNode items = events("?type=agent.status.changed&vm_id=" + vmId);
        assertThat(items).hasSize(2);
        assertThat(items.get(0).path("severity").asText()).isEqualTo("WARNING");
        assertThat(items.get(0).path("payload").path("from").asText()).isEqualTo("ONLINE");
        assertThat(items.get(0).path("payload").path("to").asText()).isEqualTo("OFFLINE");
        // OFFLINE starts when offline-after (30s) has passed since the last heartbeat.
        assertThat(Instant.parse(items.get(0).path("occurred_at").asText()))
                .isCloseTo(old.plusSeconds(30), within(1, ChronoUnit.MILLIS));
        assertThat(items.get(1).path("severity").asText()).isEqualTo("INFO");
        assertThat(items.get(1).path("payload").path("from").asText()).isEqualTo("UNKNOWN");
        assertThat(items.get(1).path("payload").path("to").asText()).isEqualTo("ONLINE");
    }

    @Test
    void invalidQueriesAreRejected() throws Exception {
        mockMvc.perform(authorized(get("/api/v1/events?type=vm.exploded")))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));
        mockMvc.perform(authorized(get("/api/v1/events?severity=info")))
                .andExpect(status().isUnprocessableEntity());
        mockMvc.perform(authorized(get("/api/v1/events?limit=501")))
                .andExpect(status().isUnprocessableEntity());
        mockMvc.perform(authorized(get("/api/v1/events?from=2026-01-02T00:00:00Z&to=2026-01-01T00:00:00Z")))
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("TIME_RANGE_INVALID"));
        mockMvc.perform(authorized(get("/api/v1/events?after_event_id=missing")))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("EVENT_CURSOR_EXPIRED"));
        mockMvc.perform(get("/api/v1/events")).andExpect(status().isUnauthorized());
    }

    private String createVm(String name) throws Exception {
        String body = """
                {"name":"%s","template_id":"debian-13-arm64","template_version":"1.0.2","vcpus":1,"memory_mib":256}
                """.formatted(name);
        String response = mockMvc.perform(authorized(post("/api/v1/vms"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(response).get("vm_id").asText();
    }

    private void acceptRecoveryEvent(int eventNumber, String type, String terminalFields) {
        byte[] payload = ("""
                {
                  "event_id":"%s",
                  "type":"%s",
                  "vm_id":"%s",
                  "recovery_id":"%s",
                  "recovery_attempt_id":"%s",
                  "attempt":1,
                  "reason":"HEARTBEAT_LOST",
                  "mode":"graceful-restart",
                  "detected_at":"2026-10-28T00:00:00Z",
                  "occurred_at":"2026-10-28T00:00:01Z"
                  %s
                }
                """).formatted(recoveryEventId(eventNumber), type, RECOVERY_VM_ID,
                        RECOVERY_OPERATION_ID, RECOVERY_CORRELATION_ID, terminalFields)
                .getBytes(StandardCharsets.UTF_8);

        assertThat(recoveryEventMessageHandler.handle(payload, Instant.parse("2026-10-28T00:00:02Z")))
                .isEqualTo(RecoveryEventMessageHandler.Result.ACCEPTED);
    }

    private static JsonNode assertRecoveryEvent(
            JsonNode items, int eventNumber, String type, String severity) {
        String eventId = recoveryEventId(eventNumber);
        JsonNode event = null;
        for (JsonNode candidate : items) {
            if (eventId.equals(candidate.path("event_id").asText())) {
                event = candidate;
                break;
            }
        }
        assertThat(event).as("REST response for %s", eventId).isNotNull();
        assertThat(event.path("type").asText()).isEqualTo(type);
        assertThat(event.path("severity").asText()).isEqualTo(severity);
        assertThat(event.path("vm_id").asText()).isEqualTo(RECOVERY_VM_ID);
        assertThat(event.path("operation_id").asText()).isEqualTo(RECOVERY_OPERATION_ID);
        assertThat(event.path("correlation_id").asText()).isEqualTo(RECOVERY_CORRELATION_ID);
        assertThat(event.path("occurred_at").asText()).isEqualTo("2026-10-28T00:00:01Z");
        assertThat(event.path("recorded_at").asText()).isEqualTo("2026-10-28T00:00:02Z");
        assertThat(event.path("payload").path("attempt").asInt()).isEqualTo(1);
        assertThat(event.path("payload").path("reason").asText()).isEqualTo("HEARTBEAT_LOST");
        assertThat(event.path("payload").path("mode").asText()).isEqualTo("graceful-restart");
        return event;
    }

    private static String recoveryEventId(int value) {
        return "00000000-0000-0000-0001-%012d".formatted(value);
    }

    private static String recoveryError() {
        return "{\"code\":\"XEN_EXECUTION_FAILED\",\"platform_code\":\"XEN_EXECUTION_FAILED\","
                + "\"message\":\"xl failed\"}";
    }

    private JsonNode events(String query) throws Exception {
        String body = mockMvc.perform(authorized(get("/api/v1/events" + query)))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(body).path("items");
    }

    private static List<String> types(JsonNode items) {
        List<String> types = new ArrayList<>();
        items.forEach(item -> types.add(item.path("type").asText()));
        return types;
    }

    private MockHttpServletRequestBuilder authorized(MockHttpServletRequestBuilder request) {
        return request.header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken);
    }

    private static final String RECOVERY_VM_ID = "00000000-0000-0000-0000-000000000002";
    private static final String RECOVERY_OPERATION_ID = "00000000-0000-0000-0000-000000000003";
    private static final String RECOVERY_CORRELATION_ID = "00000000-0000-0000-0000-000000000004";
}
