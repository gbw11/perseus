package com.xenova.backend.vm.api;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.xenova.backend.agent.application.AgentDataService;
import com.xenova.backend.agent.domain.AgentInventoryRepository;
import com.xenova.backend.agent.domain.InventoryReport;
import com.xenova.backend.agent.domain.NetworkInterface;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.domain.CreateVmCommand;
import com.xenova.backend.vm.domain.VmControlPort;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import com.xenova.backend.vm.domain.VmRuntime;
import com.xenova.backend.vm.domain.VmState;
import java.time.Instant;
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
            {"username":"vm-list-user","nickname":"VM 목록 테스트","password":"password123!"}
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
    private AgentDataService agentDataService;

    @Autowired
    private AgentInventoryRepository inventoryRepository;

    @Test
    void listWrapsItemsWithObservedAt() throws Exception {
        vmRecordRepository.deleteAll();
        inventoryRepository.deleteAll();
        vmRecordRepository.save(new VmRecord(
                "vm-1", new CreateVmCommand("alpine-sensor", "alpine", "1.2.0", 1, 256), Instant.now()));
        when(vmControlPort.list()).thenReturn(List.of(new VmRuntime("vm-1", VmState.RUNNING, 1, 256)));

        mockMvc.perform(get("/api/v1/vms")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + login()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].vm_id").value("vm-1"))
                .andExpect(jsonPath("$.items[0].name").value("alpine-sensor"))
                .andExpect(jsonPath("$.items[0].state").value("running"))
                .andExpect(jsonPath("$.items[0].agent_status").value("UNKNOWN"))
                .andExpect(jsonPath("$.items[0].ipv4").doesNotExist())
                .andExpect(jsonPath("$.observed_at").isNotEmpty());
    }

    @Test
    void ipv4ComesFromTheAgentReportAndIsNullForZephyr() throws Exception {
        vmRecordRepository.deleteAll();
        inventoryRepository.deleteAll();
        Instant now = Instant.now();
        vmRecordRepository.save(new VmRecord("vm-linux",
                new CreateVmCommand("debian-a", "debian-13-arm64", "1.0.0", 1, 256), GuestKind.LINUX, now));
        vmRecordRepository.save(new VmRecord("vm-zephyr",
                new CreateVmCommand("zephyr-a", "zephyr-xenvm-starter-arm64", "1.0.0", 1, 16),
                GuestKind.ZEPHYR, now.plusSeconds(1)));
        when(vmControlPort.list()).thenReturn(List.of(
                new VmRuntime("vm-linux", VmState.RUNNING, 1, 256),
                new VmRuntime("vm-zephyr", VmState.RUNNING, 1, 16)));
        // Loopback and IPv6 are skipped; eth0 is preferred over other interfaces.
        agentDataService.recordInventory("vm-linux", new InventoryReport(
                "boot-1", "debian", "13", "Debian GNU/Linux 13 (trixie)", "6.12.48+deb13-arm64", "debian-a",
                List.of(new NetworkInterface("lo", "00:00:00:00:00:00", List.of("127.0.0.1")),
                        new NetworkInterface("docker0", "02:42:00:00:00:01", List.of("172.17.0.1")),
                        new NetworkInterface("eth0", "02:16:3e:77:00:20", List.of("fe80::1", "192.168.77.20"))),
                "0.1.0", now), now);

        mockMvc.perform(get("/api/v1/vms")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + login()))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.items[0].guest_kind").value("linux"))
                .andExpect(jsonPath("$.items[0].ipv4").value("192.168.77.20"))
                .andExpect(jsonPath("$.items[1].guest_kind").value("zephyr"))
                .andExpect(jsonPath("$.items[1].ipv4").doesNotExist());
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
