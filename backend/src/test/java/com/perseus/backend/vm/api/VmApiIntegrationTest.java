package com.perseus.backend.vm.api;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.perseus.backend.vm.domain.VmControlPort;
import com.perseus.backend.vm.domain.VmLifecycle;
import com.perseus.backend.vm.domain.VmLaunchSpec;
import com.perseus.backend.vm.domain.VmRecordRepository;
import com.perseus.backend.vm.domain.VmRuntime;
import com.perseus.backend.vm.domain.VmState;
import java.util.Optional;
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

    @Autowired
    private VmRecordRepository vmRecordRepository;

    private String accessToken;

    @BeforeEach
    void login() throws Exception {
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
    void createAcceptsBoundaryValues() throws Exception {
        createVm("a", 1, 128).andExpect(status().isCreated());
        createVm("debian-01-" + "x".repeat(53), 4, 1024)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.vcpus").value(4))
                .andExpect(jsonPath("$.memory_mib").value(1024));
    }

    @Test
    void createAcceptsKoreanNamesWithSpaces() throws Exception {
        createVm("주행 컨트롤러 A", 1, 256)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.name").value("주행 컨트롤러 A"));
    }

    @Test
    void createRejectsControlCharactersInTheName() throws Exception {
        // The JSON escapes \t and \n arrive as a real tab and line feed in the name.
        createVm("debian\\t01", 1, 256)
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.errors[0].field").value("name"));
        createVm("debian\\n01", 1, 256)
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.errors[0].field").value("name"));
    }

    @ParameterizedTest
    @CsvSource({
            "'', 1, 256, name",
            "' 앞공백', 1, 256, name",
            "'뒤공백 ', 1, 256, name",
            "debian-01, 0, 256, vcpus",
            "debian-01, 5, 256, vcpus",
            "debian-01, 1, 127, memory_mib",
            "debian-01, 1, 1025, memory_mib"
    })
    void createRejectsOutOfRangeValues(String name, int vcpus, int memoryMib, String field) throws Exception {
        createVm(name, vcpus, memoryMib)
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"))
                .andExpect(jsonPath("$.errors[0].field").value(field));

        verify(vmControlPort, never()).launch(anyString(), any(VmLaunchSpec.class));
    }

    @Test
    void createIsIdempotentByNameAndSpec() throws Exception {
        String vmId = objectMapper.readTree(createVm("web-01", 1, 256)
                        .andExpect(status().isCreated())
                        .andExpect(jsonPath("$.name").value("web-01"))
                        .andExpect(jsonPath("$.state").value("running"))
                        .andReturn().getResponse().getContentAsString())
                .get("vm_id").asText();
        when(vmControlPort.find(vmId)).thenReturn(Optional.of(new VmRuntime(vmId, VmState.RUNNING, 1, 256)));

        createVm("web-01", 1, 256)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.vm_id").value(vmId));
        createVm("web-01", 2, 256)
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.code").value("VM_NAME_CONFLICT"));
    }

    @Test
    void stoppedVmIsListedAndStartsAgainWithTheSameVmId() throws Exception {
        String vmId = objectMapper.readTree(createVm("web-02", 1, 256)
                        .andReturn().getResponse().getContentAsString())
                .get("vm_id").asText();
        when(vmControlPort.list()).thenReturn(java.util.List.of());
        when(vmControlPort.find(vmId)).thenReturn(Optional.empty());

        mockMvc.perform(post("/api/v1/vms/" + vmId + "/stop")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.state").value("stopped"));
        mockMvc.perform(get("/api/v1/vms")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(jsonPath("$.items[0].vm_id").value(vmId))
                .andExpect(jsonPath("$.items[0].name").value("web-02"))
                .andExpect(jsonPath("$.items[0].state").value("stopped"));

        mockMvc.perform(post("/api/v1/vms/" + vmId + "/start")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.state").value("running"));
        verify(vmControlPort, org.mockito.Mockito.times(2))
                .launch(org.mockito.ArgumentMatchers.eq(vmId), any(VmLaunchSpec.class));
    }

    @Test
    void deleteRetiresAndHidesVmWithoutDiscardingItsIdentity() throws Exception {
        String vmId = objectMapper.readTree(createVm("archive-01", 1, 256)
                        .andExpect(status().isCreated())
                        .andReturn().getResponse().getContentAsString())
                .get("vm_id").asText();

        mockMvc.perform(delete("/api/v1/vms/" + vmId)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isNoContent());

        mockMvc.perform(get("/api/v1/vms/" + vmId)
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isNotFound());
        mockMvc.perform(get("/api/v1/vms")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items").isEmpty());

        var retired = vmRecordRepository.findById(vmId).orElseThrow();
        org.assertj.core.api.Assertions.assertThat(retired.getLifecycle()).isEqualTo(VmLifecycle.RETIRED);
        org.assertj.core.api.Assertions.assertThat(retired.getName()).isEqualTo("archive-01");
        verify(vmControlPort).destroy(vmId);
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
                .andExpect(jsonPath("$[0].template_id").value("debian-13-arm64"))
                .andExpect(jsonPath("$[0].version").value("1.0.0"))
                .andExpect(jsonPath("$[0].display_name").value("Debian 12"))
                .andExpect(jsonPath("$[0].guest_kind").value("linux"))
                .andExpect(jsonPath("$[1].min_memory_mib").value(512))
                .andExpect(jsonPath("$[2].template_id").value("zephyr-xenvm-starter-arm64"))
                .andExpect(jsonPath("$[2].guest_kind").value("zephyr"));
    }

    @Test
    void vmsCarryTheGuestKindOfTheirTemplate() throws Exception {
        createVm("zephyr-01", "zephyr-xenvm-starter-arm64", "1.0.0", 1, 128)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.guest_kind").value("zephyr"));
        createVm("debian-02", "debian-13-arm64", "1.0.0", 1, 256)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.guest_kind").value("linux"));

        mockMvc.perform(get("/api/v1/vms")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[?(@.name == 'zephyr-01')].guest_kind").value("zephyr"))
                .andExpect(jsonPath("$.items[?(@.name == 'zephyr-01')].agent_status").value("UNKNOWN"));
    }

    @Test
    void createRejectsUnknownTemplate() throws Exception {
        createVm("debian-01", "debian-13-arm64", "0.9.0", 1, 256)
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("TEMPLATE_NOT_FOUND"));

        verify(vmControlPort, never()).launch(anyString(), any(VmLaunchSpec.class));
    }

    @Test
    void createRejectsMemoryBelowTemplateMinimum() throws Exception {
        createVm("ubuntu-01", "ubuntu-24.04-arm64", "1.0.0", 1, 511)
                .andExpect(status().isUnprocessableEntity())
                .andExpect(jsonPath("$.code").value("VALIDATION_ERROR"));

        verify(vmControlPort, never()).launch(anyString(), any(VmLaunchSpec.class));
    }

    private org.springframework.test.web.servlet.ResultActions createVm(String name, int vcpus, int memoryMib)
            throws Exception {
        return createVm(name, "debian-13-arm64", "1.0.0", vcpus, memoryMib);
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
