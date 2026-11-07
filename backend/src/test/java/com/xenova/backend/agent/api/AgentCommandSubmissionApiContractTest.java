package com.xenova.backend.agent.api;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.xenova.backend.agent.application.AgentStressCommand;
import com.xenova.backend.agent.application.AgentStressService;
import com.xenova.backend.agent.application.AutoDelayCommand;
import com.xenova.backend.agent.application.AutoDelayService;
import java.time.Instant;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class AgentCommandSubmissionApiContractTest {

    private static final String VM_ID = "850e8400-e29b-41d4-a716-446655440001";
    private static final Instant NOW = Instant.parse("2026-11-07T06:00:00Z");

    private final AgentStressService stressService = mock(AgentStressService.class);
    private final AutoDelayService delayService = mock(AutoDelayService.class);
    private MockMvc mvc;

    @BeforeEach
    void setUp() {
        ObjectMapper mapper = new ObjectMapper().findAndRegisterModules()
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
                .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
        mvc = MockMvcBuilders.standaloneSetup(
                        new AgentStressController(stressService), new AutoDelayController(delayService))
                .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper)).build();
    }

    @Test
    void acceptsCpuLoadPercentAndReturnsTrackingIdentity() throws Exception {
        when(stressService.cpu(VM_ID, 1, 80, 30)).thenReturn(new AgentStressCommand(
                "command-1", "operation-1", VM_ID, AgentStressCommand.StressType.CPU,
                1, 80, 30, NOW, NOW.plusSeconds(30)));

        mvc.perform(post("/api/v1/vms/{vmId}/stress/cpu", VM_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"workers\":1,\"load_percent\":80,\"duration_s\":30}"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.command_id").value("command-1"))
                .andExpect(jsonPath("$.operation_id").value("operation-1"))
                .andExpect(jsonPath("$.load_percent").value(80))
                .andExpect(jsonPath("$.duration_s").value(30));

        verify(stressService).cpu(VM_ID, 1, 80, 30);
    }

    @Test
    void rejectsOutOfRangeCpuAndAutoDelayInputs() throws Exception {
        mvc.perform(post("/api/v1/vms/{vmId}/stress/cpu", VM_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"workers\":1,\"load_percent\":9,\"duration_s\":30}"))
                .andExpect(status().isBadRequest());

        mvc.perform(post("/api/v1/vms/{vmId}/faults/auto-delay", VM_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"delay_ms\":101,\"duration_s\":30}"))
                .andExpect(status().isBadRequest());
    }

    @Test
    void acceptsAutoDelayAndReturnsDelayParameters() throws Exception {
        when(delayService.send(VM_ID, 50, 30)).thenReturn(new AutoDelayCommand(
                "command-2", "operation-2", VM_ID, 50, 30, NOW, NOW.plusSeconds(30)));

        mvc.perform(post("/api/v1/vms/{vmId}/faults/auto-delay", VM_ID)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"delay_ms\":50,\"duration_s\":30}"))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.command_id").value("command-2"))
                .andExpect(jsonPath("$.delay_ms").value(50))
                .andExpect(jsonPath("$.duration_s").value(30));

        verify(delayService).send(VM_ID, 50, 30);
    }
}
