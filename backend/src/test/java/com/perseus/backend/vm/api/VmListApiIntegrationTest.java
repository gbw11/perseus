package com.perseus.backend.vm.api;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmControlPort;
import com.perseus.backend.vm.domain.VmState;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
class VmListApiIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"vm-list-user","password":"password123!"}
            """;

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @MockitoBean
    private VmControlPort vmControlPort;

    @Test
    void listWrapsItemsWithObservedAt() throws Exception {
        when(vmControlPort.list()).thenReturn(List.of(
                new Vm("vm-1", "alpine-sensor", VmState.RUNNING, "alpine", "1.2.0", 1, 256)));

        mockMvc.perform(get("/api/v1/vms")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + login()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].vm_id").value("vm-1"))
                .andExpect(jsonPath("$.items[0].state").value("running"))
                .andExpect(jsonPath("$.items[0].agent_status").value("UNKNOWN"))
                .andExpect(jsonPath("$.observed_at").isNotEmpty());
    }

    private String login() throws Exception {
        mockMvc.perform(post("/api/v1/auth/signup")
                .contentType(MediaType.APPLICATION_JSON)
                .content(CREDENTIALS));
        String body = mockMvc.perform(post("/api/v1/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(CREDENTIALS))
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(body).get("access_token").asText();
    }
}
