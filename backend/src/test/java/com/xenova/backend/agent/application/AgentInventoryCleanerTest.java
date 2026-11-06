package com.xenova.backend.agent.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.xenova.backend.agent.domain.AgentInventoryRepository;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;

class AgentInventoryCleanerTest {

    private final AgentInventoryRepository repository = mock(AgentInventoryRepository.class);
    private final List<String> cleared = new ArrayList<>();

    @Test
    void clearsTheRetainedInventoryAndDeletesTheStoredRow() {
        when(repository.existsById("vm-1")).thenReturn(true);

        new AgentInventoryCleaner(repository, () -> cleared::add).vmDeleted("vm-1");

        assertThat(cleared).containsExactly("vm-1");
        verify(repository).deleteById("vm-1");
    }

    @Test
    void withoutMqttStillDeletesTheStoredRow() {
        when(repository.existsById("vm-1")).thenReturn(true);

        new AgentInventoryCleaner(repository, () -> null).vmDeleted("vm-1");

        verify(repository).deleteById("vm-1");
    }

    @Test
    void aVmThatNeverReportedHasNothingToDelete() {
        new AgentInventoryCleaner(repository, () -> cleared::add).vmDeleted("vm-1");

        assertThat(cleared).containsExactly("vm-1");
        verify(repository, never()).deleteById("vm-1");
    }

    @Test
    void aStorageFailureDoesNotFailTheDelete() {
        when(repository.existsById("vm-1")).thenReturn(true);
        doThrow(new IllegalStateException("db down")).when(repository).deleteById("vm-1");

        assertThatCode(() -> new AgentInventoryCleaner(repository, () -> cleared::add).vmDeleted("vm-1"))
                .doesNotThrowAnyException();
        assertThat(cleared).containsExactly("vm-1");
    }
}
