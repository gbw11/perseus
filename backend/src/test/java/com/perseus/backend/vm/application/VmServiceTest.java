package com.perseus.backend.vm.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.perseus.backend.template.application.TemplateCatalog;
import com.perseus.backend.template.domain.VmTemplate;
import com.perseus.backend.template.infrastructure.TemplateCatalogProperties;
import com.perseus.backend.vm.domain.CreateVmCommand;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmControlPort;
import com.perseus.backend.vm.domain.VmState;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class VmServiceTest {

    @Mock
    private VmControlPort vmControlPort;

    private final TemplateCatalog templateCatalog = new TemplateCatalog(new TemplateCatalogProperties(
            List.of(new VmTemplate("alpine", "1.2.0", "Alpine 1.2.0", 128))));

    @Test
    void createGeneratesUuidAndDelegatesToControlLayer() {
        CreateVmCommand command = new CreateVmCommand("alpine-sensor", "alpine", "1.2.0", 1, 256);
        when(vmControlPort.create(anyString(), eq(command))).thenAnswer(invocation -> new Vm(
                invocation.getArgument(0),
                command.name(),
                VmState.SHUTDOWN,
                command.templateId(),
                command.templateVersion(),
                command.vcpus(),
                command.memoryMib()));

        Vm vm = new VmService(vmControlPort, templateCatalog).create(command);

        ArgumentCaptor<String> vmIdCaptor = ArgumentCaptor.forClass(String.class);
        verify(vmControlPort).create(vmIdCaptor.capture(), eq(command));
        assertThat(UUID.fromString(vmIdCaptor.getValue()).toString()).isEqualTo(vm.vmId());
        assertThat(vm.state()).isEqualTo(VmState.SHUTDOWN);
    }
}
