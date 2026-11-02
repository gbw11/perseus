package com.xenova.backend.vm.api;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.xenova.backend.vm.domain.VmControlPort;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import org.junit.jupiter.api.BeforeEach;
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
class VmRegistrationApiIntegrationTest {

    private static final String TOKEN = "test-registration-token";
    private static final String VM_ID = "64b1ab38-89cc-4454-8d94-b8201f4cdd33";
    private static final String BODY = """
            {
              "schema_version":"1.0",
              "ok":true,
              "request_id":"0c6e71a6-9f82-41f4-b282-749bbe521aa2",
              "operation_id":null,
              "replayed":false,
              "data":{
                "plan":true,
                "execute_required":true,
                "registration":{
                  "schema_version":"1.0",
                  "vm_id":"64b1ab38-89cc-4454-8d94-b8201f4cdd33",
                  "name":"zephyr-drive-a",
                  "guest_kind":"zephyr",
                  "template_id":"zephyr-xenvm-starter-arm64",
                  "template_version":"1.1.0",
                  "vcpus":1,
                  "memory_mib":16,
                  "assigned_ipv4":"192.168.77.21"
                }
              },
              "error":null
            }
            """;

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private VmRecordRepository repository;

    @MockitoBean
    private VmControlPort vmControlPort;

    @BeforeEach
    void clear() {
        repository.deleteAll();
    }

    @Test
    void registersPlatformVmWithoutCallingCreateOrStartAndReplaysIdempotently() throws Exception {
        send(BODY, TOKEN)
                .andExpect(status().isCreated())
                .andExpect(jsonPath("$.vm_id").value(VM_ID))
                .andExpect(jsonPath("$.registration").value("confirmed"));

        VmRecord record = repository.findById(VM_ID).orElseThrow();
        assertThat(record.getName()).isEqualTo("zephyr-drive-a");
        assertThat(record.getTemplateId()).isEqualTo("zephyr-xenvm-starter-arm64");
        assertThat(record.getTemplateVersion()).isEqualTo("1.1.0");
        assertThat(record.getVcpus()).isEqualTo(1);
        assertThat(record.getMemoryMib()).isEqualTo(16);
        assertThat(record.getGuestKind().value()).isEqualTo("zephyr");
        assertThat(record.getAssignedIpv4()).isEqualTo("192.168.77.21");

        send(BODY, TOKEN)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.registration").value("confirmed"));
        assertThat(repository.count()).isEqualTo(1);
        verifyNoInteractions(vmControlPort);
    }

    @Test
    void rejectsMissingAndWrongHostTokens() throws Exception {
        send(BODY, null).andExpect(status().isUnauthorized());
        send(BODY, "wrong-token").andExpect(status().isForbidden());
        assertThat(repository.count()).isZero();
    }

    @Test
    void rejectsInvalidBodyAndDoesNotOverwriteAnExistingVmOnReplay() throws Exception {
        send(BODY.replace("\"vcpus\":1", "\"vcpus\":0"), TOKEN)
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("INVALID_REGISTRATION"));

        send(BODY, TOKEN).andExpect(status().isCreated());
        send(BODY.replace("zephyr-drive-a", "different-name"), TOKEN)
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.registration").value("confirmed"));

        assertThat(repository.findById(VM_ID).orElseThrow().getName()).isEqualTo("zephyr-drive-a");
        assertThat(repository.count()).isEqualTo(1);
    }

    @Test
    void acceptsNullTemplateVersionForLegacyRegistration() throws Exception {
        send(BODY.replace("\"1.1.0\"", "null"), TOKEN).andExpect(status().isCreated());
        assertThat(repository.findById(VM_ID).orElseThrow().getTemplateVersion()).isNull();
    }

    private org.springframework.test.web.servlet.ResultActions send(String body, String token) throws Exception {
        var request = post("/api/v1/vms/registrations")
                .contentType(MediaType.APPLICATION_JSON)
                .content(body);
        if (token != null) {
            request.header(HttpHeaders.AUTHORIZATION, "Bearer " + token);
        }
        return mockMvc.perform(request);
    }
}

