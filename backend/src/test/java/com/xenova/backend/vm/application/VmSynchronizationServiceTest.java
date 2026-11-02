package com.xenova.backend.vm.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.vm.api.VmSynchronizationResponse;
import com.xenova.backend.vm.domain.CreateVmCommand;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import com.xenova.backend.vm.domain.VmRegistrationInventory;
import com.xenova.backend.vm.domain.VmRegistrationInventoryPort;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class VmSynchronizationServiceTest {

    private static final String BOARD_ONLY_ID = "850e8400-e29b-41d4-a716-446655440001";
    private static final String EXISTING_ID = "850e8400-e29b-41d4-a716-446655440002";
    private static final String DB_ONLY_ID = "850e8400-e29b-41d4-a716-446655440003";

    @Mock
    private VmRegistrationInventoryPort inventoryPort;

    @Mock
    private VmRecordRepository repository;

    private VmSynchronizationService service;

    @BeforeEach
    void setUp() {
        service = new VmSynchronizationService(inventoryPort, repository,
                Clock.fixed(Instant.parse("2026-11-02T00:00:00Z"), ZoneOffset.UTC));
    }

    @Test
    void registersBoardOnlyVmUpdatesChangedVmAndReportsDatabaseOnlyVm() {
        VmRecord existing = record(EXISTING_ID, "existing");
        VmRecord databaseOnly = record(DB_ONLY_ID, "database-only");
        when(repository.findAll()).thenReturn(List.of(existing, databaseOnly));
        when(inventoryPort.listRegistrations()).thenReturn(List.of(
                inventory(BOARD_ONLY_ID, "board-only"),
                inventory(EXISTING_ID, "stale-board-name")));
        when(repository.save(any(VmRecord.class))).thenAnswer(invocation -> invocation.getArgument(0));

        VmSynchronizationResponse response = service.synchronize();

        assertThat(response.discovered()).isEqualTo(2);
        assertThat(response.registered()).isEqualTo(1);
        assertThat(response.updated()).isEqualTo(1);
        assertThat(response.unchanged()).isZero();
        assertThat(response.missingFromBoard()).isEqualTo(1);
        assertThat(existing.getName()).isEqualTo("stale-board-name");
        assertThat(databaseOnly.getName()).isEqualTo("database-only");
        assertThat(response.results()).extracting(VmSynchronizationResponse.Item::action)
                .containsExactly("registered", "updated", "missing_from_board");

        ArgumentCaptor<VmRecord> saved = ArgumentCaptor.forClass(VmRecord.class);
        verify(repository, org.mockito.Mockito.times(2)).save(saved.capture());
        assertThat(saved.getAllValues()).extracting(VmRecord::getVmId)
                .containsExactly(BOARD_ONLY_ID, EXISTING_ID);
        assertThat(saved.getAllValues().getFirst().getAssignedIpv4()).isEqualTo("192.168.77.30");
    }

    @Test
    void reportsOldDom0ResponseAsIncompleteInsteadOfCreatingAPartialRow() {
        when(repository.findAll()).thenReturn(List.of());
        when(inventoryPort.listRegistrations()).thenReturn(List.of(
                new VmRegistrationInventory(BOARD_ONLY_ID, null, null, null, null, 1, 512, null)));

        VmSynchronizationResponse response = service.synchronize();

        assertThat(response.registered()).isZero();
        assertThat(response.incomplete()).isEqualTo(1);
        assertThat(response.results()).singleElement().satisfies(item -> {
            assertThat(item.action()).isEqualTo("incomplete");
            assertThat(item.reason()).isEqualTo("registration_metadata_missing");
        });
    }

    private static VmRegistrationInventory inventory(String vmId, String name) {
        return new VmRegistrationInventory(
                vmId, name, GuestKind.LINUX, "debian-13-arm64", "1.0.2",
                1, 512, "192.168.77.30");
    }

    private static VmRecord record(String vmId, String name) {
        return new VmRecord(
                vmId, new CreateVmCommand(name, "debian-13-arm64", "1.0.2", 1, 512),
                GuestKind.LINUX, Instant.parse("2026-11-01T00:00:00Z"));
    }
}
