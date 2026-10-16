package com.perseus.backend.vm.api;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.perseus.backend.vm.domain.CreateVmCommand;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmControlPort;
import com.perseus.backend.vm.domain.VmState;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
class VmApiIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"vm-api-user","password":"password123!"}
            """;

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @MockitoBean
    private VmControlPort vmControlPort;

    private String accessToken;

    @BeforeEach
    void login() throws Exception {
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
    void createAcceptsBoundaryValues() throws Exception {
        when(vmControlPort.create(anyString(), any(CreateVmCommand.class))).thenAnswer(invocation -> {
            CreateVmCommand command = invocation.getArgument(1);
            return new Vm(invocation.getArgument(0), command.name(), VmState.SHUTDOWN,
                    command.templateId(), command.templateVersion(), command.vcpus(), command.memoryMib());
        });

        createVm("a", 1, 100).andExpect(status().isCreated());
        createVm("debian-01-" + "x".repeat(53), 4, 1024)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.vcpus").value(4))
                .andExpect(jsonPath("$.memory_mib").value(1024));
    }

    @ParameterizedTest
    @CsvSource({
            "Debian-01, 1, 256, name",
            "debian_01, 1, 256, name",
            "'', 1, 256, name",
            "debian-01, 0, 256, vcpus",
            "debian-01, 5, 256, vcpus",
            "debian-01, 1, 99, memory_mib",
            "debian-01, 1, 1025, memory_mib"
    })
    void createRejectsOutOfRangeValues(String name, int vcpus, int memoryMib, String field) throws Exception {
        createVm(name, vcpus, memoryMib)
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.errors[0].field").value(field));

        verify(vmControlPort, never()).create(anyString(), any(CreateVmCommand.class));
    }

    @Test
    void createRejectsNameLongerThan63Characters() throws Exception {
        createVm("a".repeat(64), 1, 256)
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.errors[0].field").value("name"));
    }

    @Test
    void templatesAreListedInSnakeCase() throws Exception {
        mockMvc.perform(get("/api/v1/templates")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].template_id").value("debian"))
                .andExpect(jsonPath("$[0].version").value("12"))
                .andExpect(jsonPath("$[0].display_name").value("Debian 12"))
                .andExpect(jsonPath("$[1].min_memory_mib").value(512));
    }

    @Test
    void createRejectsUnknownTemplate() throws Exception {
        createVm("debian-01", "debian", "11", 1, 256)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("TEMPLATE_NOT_FOUND"));

        verify(vmControlPort, never()).create(anyString(), any(CreateVmCommand.class));
    }

    @Test
    void createRejectsMemoryBelowTemplateMinimum() throws Exception {
        createVm("ubuntu-01", "ubuntu", "24.04", 1, 511)
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));

        verify(vmControlPort, never()).create(anyString(), any(CreateVmCommand.class));
    }

    private org.springframework.test.web.servlet.ResultActions createVm(String name, int vcpus, int memoryMib)
            throws Exception {
        return createVm(name, "debian", "12", vcpus, memoryMib);
    }

    private org.springframework.test.web.servlet.ResultActions createVm(
            String name, String templateId, String templateVersion, int vcpus, int memoryMib) throws Exception {
        String body = """
                {"name":"%s","template_id":"%s","template_version":"%s","vcpus":%d,"memory_mib":%d}
                """.formatted(name, templateId, templateVersion, vcpus, memoryMib);
        return mockMvc.perform(post("/api/v1/vms")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken)
                .contentType(MediaType.APPLICATION_JSON)
                .content(body));
    }
}
