package com.xenova.backend.vm.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.xenova.backend.agent.application.AgentInventoryCleaner;
import com.xenova.backend.event.application.EventRecorder;
import com.xenova.backend.event.application.VmStateTracker;
import com.xenova.backend.template.application.TemplateCatalog;
import com.xenova.backend.template.domain.GuestKind;
import com.xenova.backend.template.domain.VmTemplate;
import com.xenova.backend.template.infrastructure.TemplateCatalogProperties;
import com.xenova.backend.vm.domain.CreateVmCommand;
import com.xenova.backend.vm.domain.Vm;
import com.xenova.backend.vm.domain.VmControlPort;
import com.xenova.backend.vm.domain.VmLaunchSpec;
import com.xenova.backend.vm.domain.VmLifecycle;
import com.xenova.backend.vm.domain.VmRecord;
import com.xenova.backend.vm.domain.VmRecordRepository;
import com.xenova.backend.vm.domain.VmRuntime;
import com.xenova.backend.vm.domain.VmState;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

// VmService with the runtime cache on: screens read the cached list, operations update it.
class VmServiceRuntimeCacheTest {

    private static final Instant START = Instant.parse("2026-11-02T08:00:00Z");

    private final VmRuntimeCacheTest.MutableClock clock = new VmRuntimeCacheTest.MutableClock(START);
    private VmControlPort port;
    private VmRecordRepository repository;
    private VmService service;
    private VmRecord record;

    @BeforeEach
    void setUp() {
        port = mock(VmControlPort.class);
        repository = mock(VmRecordRepository.class);
        TemplateCatalog templates = new TemplateCatalog(new TemplateCatalogProperties(
                List.of(new VmTemplate("alpine", "1.2.0", "Alpine 1.2.0", 128, GuestKind.LINUX))));
        VmRuntimeCache cache = new VmRuntimeCache(
                port, new VmRuntimeCacheProperties(true, Duration.ofSeconds(1), Duration.ofSeconds(10)), clock);
        service = new VmService(port, repository, templates, mock(EventRecorder.class), mock(VmStateTracker.class),
                cache, mock(AgentInventoryCleaner.class), clock);
        record = new VmRecord("vm-1", new CreateVmCommand("alpine-1", "alpine", "1.2.0", 1, 256), GuestKind.LINUX, START);
        when(repository.findAllByLifecycleOrLifecycleIsNullOrderByCreatedAtAsc(VmLifecycle.REGISTERED))
                .thenReturn(List.of(record));
        when(repository.findById("vm-1")).thenReturn(Optional.of(record));
    }

    @Test
    void listAndGetAnswerFromTheRefreshedListWithItsObservedTime() {
        when(port.list()).thenReturn(List.of(new VmRuntime("vm-1", VmState.RUNNING, 1, 256)));

        service.listLive();
        clock.advance(Duration.ofSeconds(3));
        VmService.VmList list = service.listObserved();

        assertThat(list.vms()).extracting(Vm::state).containsExactly(VmState.RUNNING);
        assertThat(list.observedAt()).isEqualTo(START);
        assertThat(service.get("vm-1").state()).isEqualTo(VmState.RUNNING);
        verify(port, times(1)).list();
        verify(port, never()).find("vm-1");
    }

    @Test
    void stoppedVmIsShownStoppedRightAwayWithoutAnotherList() {
        when(port.list()).thenReturn(List.of(new VmRuntime("vm-1", VmState.RUNNING, 1, 256)));
        when(port.find("vm-1")).thenReturn(Optional.of(new VmRuntime("vm-1", VmState.RUNNING, 1, 256)));
        service.listLive();

        service.stop("vm-1");

        assertThat(service.list()).extracting(Vm::state).containsExactly(VmState.STOPPED);
        verify(port, times(1)).list();
    }

    @Test
    void startedVmIsShownRunningRightAwayWithoutAnotherList() {
        when(port.list()).thenReturn(List.of());
        when(port.find("vm-1")).thenReturn(Optional.empty());
        when(port.launch(eq("vm-1"), any(VmLaunchSpec.class))).thenReturn(new VmRuntime("vm-1", VmState.RUNNING, 1, 256));
        service.listLive();

        service.start("vm-1");

        assertThat(service.list()).extracting(Vm::state).containsExactly(VmState.RUNNING);
        verify(port, times(1)).list();
    }
}
