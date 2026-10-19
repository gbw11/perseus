package com.perseus.backend.report.os;

import static org.hamcrest.Matchers.hasItem;
import static org.hamcrest.Matchers.startsWith;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.perseus.backend.agent.application.AgentDataService;
import com.perseus.backend.agent.domain.AgentHeartbeatRepository;
import com.perseus.backend.agent.domain.AgentInventoryRepository;
import com.perseus.backend.agent.domain.InventoryReport;
import com.perseus.backend.agent.domain.NetworkInterface;
import com.perseus.backend.common.error.ApiException;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmControlPort;
import com.perseus.backend.vm.domain.VmState;
import java.time.Instant;
import java.util.List;
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
import org.springframework.test.web.servlet.ResultActions;

@SpringBootTest
@AutoConfigureMockMvc
class OsEvidenceReportApiIntegrationTest {

    private static final String CREDENTIALS = """
            {"username":"report-os-user","password":"password123!"}
            """;
    private static final List<Vm> VMS = List.of(
            new Vm("vm-debian", "debian-01", VmState.RUNNING, "debian", "12", 1, 256),
            new Vm("vm-ubuntu", "ubuntu-01", VmState.RUNNING, "ubuntu", "24.04", 1, 512),
            new Vm("vm-alpine", "alpine-01", VmState.RUNNING, "alpine", "1.2.0", 1, 128));

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private ObjectMapper objectMapper;

    @Autowired
    private AgentDataService agentDataService;

    @Autowired
    private AgentInventoryRepository inventoryRepository;

    @Autowired
    private AgentHeartbeatRepository heartbeatRepository;

    @MockitoBean
    private VmControlPort vmControlPort;

    private String accessToken;

    @BeforeEach
    void setUp() throws Exception {
        inventoryRepository.deleteAll();
        heartbeatRepository.deleteAll();
        mockMvc.perform(post("/api/v1/auth/signup")
                .contentType(MediaType.APPLICATION_JSON)
                .content(CREDENTIALS));
        String body = mockMvc.perform(post("/api/v1/auth/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content(CREDENTIALS))
                .andReturn().getResponse().getContentAsString();
        accessToken = objectMapper.readTree(body).get("access_token").asText();
        when(vmControlPort.list()).thenReturn(VMS);
    }

    @Test
    void passesWhenAllRequiredOsReportThemselvesWhileRunning() throws Exception {
        reportAgent("vm-debian", "debian", "Debian GNU/Linux 12 (bookworm)", Instant.now());
        reportAgent("vm-ubuntu", "ubuntu", "Ubuntu 24.04 LTS", Instant.now());
        reportAgent("vm-alpine", "alpine", "Alpine Linux v3.20", Instant.now());

        report()
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.verdict").value("PASS"))
                .andExpect(jsonPath("$.reasons").isEmpty())
                .andExpect(jsonPath("$.build_id").value("test-build"))
                .andExpect(jsonPath("$.required_os_ids.length()").value(3))
                .andExpect(jsonPath("$.observation_window.from").isNotEmpty())
                .andExpect(jsonPath("$.entries.length()").value(3))
                .andExpect(jsonPath("$.entries[0].xen_state").value("running"))
                .andExpect(jsonPath("$.entries[0].agent_status").value("ONLINE"))
                .andExpect(jsonPath("$.entries[0].expected_os.id").value("debian"))
                .andExpect(jsonPath("$.entries[0].observed_os.pretty_name").value("Debian GNU/Linux 12 (bookworm)"))
                .andExpect(jsonPath("$.entries[0].interfaces[0].ips[0]").value("10.0.0.11"))
                .andExpect(jsonPath("$.entries[0].match").value("MATCH"));
    }

    @Test
    void failsWhenAVmReportsADifferentOs() throws Exception {
        reportAgent("vm-debian", "debian", "Debian GNU/Linux 12 (bookworm)", Instant.now());
        reportAgent("vm-ubuntu", "debian", "Debian GNU/Linux 12 (bookworm)", Instant.now());
        reportAgent("vm-alpine", "alpine", "Alpine Linux v3.20", Instant.now());

        report()
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.verdict").value("FAIL"))
                .andExpect(jsonPath("$.entries[1].match").value("MISMATCH"))
                .andExpect(jsonPath("$.reasons", hasItem(startsWith("ubuntu VM"))));
    }

    @Test
    void isBlockedWhenAnAgentStoppedReporting() throws Exception {
        reportAgent("vm-debian", "debian", "Debian GNU/Linux 12 (bookworm)", Instant.now());
        reportAgent("vm-ubuntu", "ubuntu", "Ubuntu 24.04 LTS", Instant.now());
        reportAgent("vm-alpine", "alpine", "Alpine Linux v3.20", Instant.now().minusSeconds(60));

        report()
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.verdict").value("BLOCKED"))
                .andExpect(jsonPath("$.entries[2].agent_status").value("OFFLINE"))
                .andExpect(jsonPath("$.reasons", hasItem(startsWith("alpine VM"))));
    }

    @Test
    void isBlockedWithoutAgentReports() throws Exception {
        report()
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.verdict").value("BLOCKED"))
                .andExpect(jsonPath("$.entries[0].agent_status").value("UNKNOWN"))
                .andExpect(jsonPath("$.entries[0].observed_os").isEmpty())
                .andExpect(jsonPath("$.entries[0].match").value("UNKNOWN"));
    }

    @Test
    void isBlockedWhenVmControlIsUnavailable() throws Exception {
        when(vmControlPort.list()).thenThrow(new ApiException(
                HttpStatus.SERVICE_UNAVAILABLE, "VM_CONTROL_UNAVAILABLE", "VM 제어 서비스에 연결할 수 없습니다."));

        report()
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.verdict").value("BLOCKED"))
                .andExpect(jsonPath("$.entries").isEmpty())
                .andExpect(jsonPath("$.reasons[0]", startsWith("VM 제어 서비스")));
    }

    private ResultActions report() throws Exception {
        return mockMvc.perform(get("/api/v1/reports/os-evidence")
                .header(HttpHeaders.AUTHORIZATION, "Bearer " + accessToken));
    }

    private void reportAgent(String vmId, String osId, String prettyName, Instant receivedAt) {
        String bootId = vmId + "-boot-1";
        agentDataService.recordInventory(vmId, new InventoryReport(
                bootId, osId, "1", prettyName, "6.6.31", vmId + "-host",
                List.of(new NetworkInterface("eth0", "00:16:3e:00:00:01", List.of("10.0.0.11"))),
                "0.1.0", receivedAt), receivedAt);
        agentDataService.recordHeartbeat(vmId, bootId, "agent-1", 1L, receivedAt, receivedAt);
    }
}
