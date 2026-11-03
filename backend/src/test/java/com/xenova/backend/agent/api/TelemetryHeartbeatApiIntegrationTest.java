package com.xenova.backend.agent.api;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore;
import com.xenova.backend.agent.application.TelemetryHeartbeatStore.Heartbeat;
import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
class TelemetryHeartbeatApiIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"heartbeat-user","nickname":"하트비트 테스트","password":"password123!"}
            """;
    private static final String VM = "f1000000-0000-4000-8000-0000000000a1";
    private static final String UNSEEN_VM = "f1000000-0000-4000-8000-0000000000a2";

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private TelemetryHeartbeatStore store;

    private String accessToken;

    @BeforeEach
    void setUp() throws Exception {
        mockMvc.perform(post("/api/v1/auth/signup")
                .contentType(MediaType.APPLICATION_JSON)
                .content(CREDENTIALS));
        String body = mockMvc.perform(post("/api/v1/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(CREDENTIALS))
                .andReturn().getResponse().getContentAsString();
        accessToken = objectMapper.readTree(body).get("access_token").asText();
    }

    @Test
    void returnsLatestHeartbeatInSnakeCase() throws Exception {
        store.offer(new Heartbeat(VM, "boot-1", "instance-1", 3, "running", 1200L, 0L, 0L, 1L, Instant.now()),
                false);

        mockMvc.perform(get("/api/v1/vms/" + VM + "/telemetry/heartbeat")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.vm_id").value(VM))
                .andExpect(jsonPath("$.status").value("FRESH"))
                .andExpect(jsonPath("$.publisher_state").value("running"))
                .andExpect(jsonPath("$.last_collection_success_age_ms").value(1200))
                .andExpect(jsonPath("$.collection_failures_total").value(0))
                .andExpect(jsonPath("$.connection_attempts_total").value(1))
                .andExpect(jsonPath("$.boot_id").value("boot-1"))
                .andExpect(jsonPath("$.age_ms").isNumber());
    }

    @Test
    void reportsUnknownBeforeAnyHeartbeat() throws Exception {
        mockMvc.perform(get("/api/v1/vms/" + UNSEEN_VM + "/telemetry/heartbeat")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.status").value("UNKNOWN"))
                .andExpect(jsonPath("$.publisher_state").doesNotExist());
    }

    @Test
    void requiresLogin() throws Exception {
        mockMvc.perform(get("/api/v1/vms/" + VM + "/telemetry/heartbeat"))
                .andExpect(status().isUnauthorized());
    }
}
