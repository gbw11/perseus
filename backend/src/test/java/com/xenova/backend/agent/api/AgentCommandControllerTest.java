package com.xenova.backend.agent.api;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.xenova.backend.agent.application.AgentCommandQueryService;
import com.xenova.backend.agent.application.AgentCommandStatusResponse;
import com.xenova.backend.agent.domain.AgentCommandStatus;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class AgentCommandControllerTest {

    @Test
    void returnsCommandStatusUsingSnakeCase() throws Exception {
        String vmId = "850e8400-e29b-41d4-a716-446655440001";
        String commandId = "command-1";
        Instant submittedAt = Instant.parse("2026-11-07T06:00:00Z");
        AgentCommandQueryService service = mock(AgentCommandQueryService.class);
        when(service.get(vmId, commandId)).thenReturn(new AgentCommandStatusResponse(
                vmId, commandId, "operation-1", AgentCommandStatus.PENDING, submittedAt,
                null, null, null, null, null, submittedAt, false, null));
        ObjectMapper mapper = new ObjectMapper().findAndRegisterModules()
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
                .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE);
        MockMvc mvc = MockMvcBuilders.standaloneSetup(new AgentCommandController(service))
                .setMessageConverters(new MappingJackson2HttpMessageConverter(mapper)).build();

        mvc.perform(get("/api/v1/vms/{vmId}/commands/{commandId}", vmId, commandId))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.vm_id").value(vmId))
                .andExpect(jsonPath("$.command_id").value(commandId))
                .andExpect(jsonPath("$.operation_id").value("operation-1"))
                .andExpect(jsonPath("$.status").value("PENDING"))
                .andExpect(jsonPath("$.submitted_at").value("2026-11-07T06:00:00Z"))
                .andExpect(jsonPath("$.result").doesNotExist())
                .andExpect(jsonPath("$.error").doesNotExist())
                .andExpect(jsonPath("$.contract_violation").value(false));
    }
}
