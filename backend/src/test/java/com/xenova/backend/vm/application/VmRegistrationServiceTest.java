package com.xenova.backend.vm.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.vm.api.VmRegistrationRequest;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class VmRegistrationServiceTest {

    private static final String VM_ID = "850e8400-e29b-41d4-a716-446655440001";

    @Mock
    private VmRecordRepository repository;

    private VmRegistrationService service;

    @BeforeEach
    void setUp() {
        service = new VmRegistrationService(repository,
                Clock.fixed(Instant.parse("2026-10-29T00:00:00Z"), ZoneOffset.UTC));
    }

    @Test
    void createsVmFromRegistrationNestedInTheCliEnvelope() {
        when(repository.findById(VM_ID)).thenReturn(Optional.empty());
        when(repository.findByName("debian-fixture")).thenReturn(Optional.empty());
        when(repository.save(any(VmRecord.class))).thenAnswer(invocation -> invocation.getArgument(0));

        VmRegistrationService.Result result = service.register(request("debian-fixture", 2));

        assertThat(result.created()).isTrue();
        ArgumentCaptor<VmRecord> saved = ArgumentCaptor.forClass(VmRecord.class);
        verify(repository).save(saved.capture());
        assertThat(saved.getValue().getVmId()).isEqualTo(VM_ID);
        assertThat(saved.getValue().getName()).isEqualTo("debian-fixture");
        assertThat(saved.getValue().getTemplateId()).isEqualTo("debian-arm64-fixture");
        assertThat(saved.getValue().getAssignedIpv4()).isEqualTo("192.168.77.30");
    }

    @Test
    void replayUsesVmIdAndDoesNotOverwriteTheExistingRow() {
        VmRecord existing = VmRecord.registeredByPlatform(
                VM_ID, "current-name", "current-template", "2.0.0", 1, 256,
                com.xenova.backend.template.domain.GuestKind.LINUX,
                Instant.parse("2026-10-28T00:00:00Z"), null);
        when(repository.findById(VM_ID)).thenReturn(Optional.of(existing));

        VmRegistrationService.Result result = service.register(request("old-name", 2));

        assertThat(result.created()).isFalse();
        assertThat(existing.getName()).isEqualTo("current-name");
        verify(repository, never()).save(any());
    }

    @Test
    void rejectsAnUnsuccessfulEnvelope() {
        VmRegistrationRequest request = request("debian-fixture", 2);
        request = new VmRegistrationRequest(
                request.schemaVersion(), false, request.requestId(), request.operationId(),
                request.replayed(), request.data(), "failed");

        VmRegistrationRequest failed = request;
        assertThatThrownBy(() -> service.register(failed))
                .isInstanceOfSatisfying(ApiException.class,
                        exception -> assertThat(exception.getCode()).isEqualTo("INVALID_REGISTRATION"));
        verify(repository, never()).save(any());
    }

    private static VmRegistrationRequest request(String name, int vcpus) {
        VmRegistrationRequest.Registration registration = new VmRegistrationRequest.Registration(
                "1.0", VM_ID, name, "linux", "debian-arm64-fixture", "1.0.2",
                vcpus, 512, "192.168.77.30");
        return new VmRegistrationRequest(
                "1.0", true, "0c6e71a6-9f82-41f4-b282-749bbe521aa2", null, false,
                new VmRegistrationRequest.Data(true, true, registration), null);
    }
}
