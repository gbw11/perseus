package com.perseus.backend.event.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.perseus.backend.common.error.ApiException;
import com.perseus.backend.event.domain.ReportedVmStateRepository;
import com.perseus.backend.event.domain.VmEventRepository;
import com.perseus.backend.vm.domain.VmControlPort;
import com.perseus.backend.vm.domain.VmLaunchSpec;
import com.perseus.backend.vm.domain.VmRecordRepository;
import com.perseus.backend.vm.domain.VmRuntime;
import com.perseus.backend.vm.domain.VmState;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;

@SpringBootTest
@AutoConfigureMockMvc
class VmStateWatcherIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"vm-state-watcher-user","password":"password123!"}
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
    private ReportedVmStateRepository reportedVmStateRepository;

    @Autowired
    private VmStateWatcher watcher;

    private String accessToken;

    @BeforeEach
    void setUp() throws Exception {
        vmRecordRepository.deleteAll();
        vmEventRepository.deleteAll();
        reportedVmStateRepository.deleteAll();
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
    void aCrashIsRecordedOnceAsADetectedError() throws Exception {
        String vmId = createVm("watch-crash-01");
        when(vmControlPort.list()).thenReturn(List.of(new VmRuntime(vmId, VmState.CRASHED, 1, 256)));

        watcher.check();
        watcher.check();

        JsonNode changes = stateChanges(vmId);
        assertThat(changes).hasSize(1);
        JsonNode crash = changes.get(0);
        assertThat(crash.path("severity").asText()).isEqualTo("ERROR");
        assertThat(crash.path("payload").path("from").asText()).isEqualTo("running");
        assertThat(crash.path("payload").path("to").asText()).isEqualTo("crashed");
        assertThat(crash.path("payload").path("cause").asText()).isEqualTo("detected");
    }

    @Test
    void aVmThatShutItselfDownIsRecordedAsStopped() throws Exception {
        String vmId = createVm("watch-off-01");
        when(vmControlPort.list()).thenReturn(List.of());

        watcher.check();

        JsonNode changes = stateChanges(vmId);
        assertThat(changes).hasSize(1);
        assertThat(changes.get(0).path("severity").asText()).isEqualTo("INFO");
        assertThat(changes.get(0).path("payload").path("from").asText()).isEqualTo("running");
        assertThat(changes.get(0).path("payload").path("to").asText()).isEqualTo("stopped");
        assertThat(changes.get(0).path("payload").path("cause").asText()).isEqualTo("detected");
    }

    @Test
    void aUserStopIsNotReportedAgainByTheCheck() throws Exception {
        String vmId = createVm("watch-user-01");
        when(vmControlPort.find(vmId)).thenReturn(Optional.of(new VmRuntime(vmId, VmState.RUNNING, 1, 256)));
        mockMvc.perform(authorized(post("/api/v1/vms/" + vmId + "/stop"))).andExpect(status().isOk());
        when(vmControlPort.list()).thenReturn(List.of());

        watcher.check();

        JsonNode changes = stateChanges(vmId);
        assertThat(changes).hasSize(1);
        assertThat(changes.get(0).path("payload").path("cause").asText()).isEqualTo("user_stop");
    }

    @Test
    void theFirstSightingOnlySetsTheBaseline() throws Exception {
        String vmId = createVm("watch-base-01");
        // As after a server restart: the last reported state is unknown.
        reportedVmStateRepository.deleteAll();
        when(vmControlPort.list()).thenReturn(List.of(new VmRuntime(vmId, VmState.CRASHED, 1, 256)));
        watcher.check();
        assertThat(stateChanges(vmId)).isEmpty();

        when(vmControlPort.list()).thenReturn(List.of(new VmRuntime(vmId, VmState.RUNNING, 1, 256)));
        watcher.check();

        JsonNode changes = stateChanges(vmId);
        assertThat(changes).hasSize(1);
        assertThat(changes.get(0).path("payload").path("from").asText()).isEqualTo("crashed");
        assertThat(changes.get(0).path("payload").path("to").asText()).isEqualTo("running");
    }

    @Test
    void nothingIsRecordedWhenDom0ManagerIsUnreachable() throws Exception {
        String vmId = createVm("watch-down-01");
        when(vmControlPort.list()).thenThrow(new ApiException(
                HttpStatus.SERVICE_UNAVAILABLE, "VM_CONTROL_UNAVAILABLE", "VM 제어 서비스에 연결할 수 없습니다."));

        watcher.check();

        assertThat(stateChanges(vmId)).isEmpty();
    }

    private String createVm(String name) throws Exception {
        String body = """
                {"name":"%s","template_id":"debian","template_version":"12","vcpus":1,"memory_mib":256}
                """.formatted(name);
        String response = mockMvc.perform(authorized(post("/api/v1/vms"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(body))
                .andExpect(status().isCreated())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(response).get("vm_id").asText();
    }

    private JsonNode stateChanges(String vmId) throws Exception {
        String body = mockMvc.perform(authorized(get("/api/v1/events?type=vm.state.changed&vm_id=" + vmId)))
                .andExpect(status().isOk())
                .andReturn().getResponse().getContentAsString();
        return objectMapper.readTree(body).path("items");
    }

    private MockHttpServletRequestBuilder authorized(MockHttpServletRequestBuilder request) {
        return request.header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken);
    }
}
