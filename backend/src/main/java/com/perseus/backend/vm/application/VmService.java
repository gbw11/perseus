package com.perseus.backend.vm.application;

import com.perseus.backend.common.error.ApiException;
import com.perseus.backend.template.application.TemplateCatalog;
import com.perseus.backend.template.domain.VmTemplate;
import com.perseus.backend.vm.domain.CreateVmCommand;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmControlPort;
import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

@Service
public class VmService {

    private final VmControlPort vmControlPort;
    private final TemplateCatalog templateCatalog;

    public VmService(VmControlPort vmControlPort, TemplateCatalog templateCatalog) {
        this.vmControlPort = vmControlPort;
        this.templateCatalog = templateCatalog;
    }

    public List<Vm> list() {
        return vmControlPort.list();
    }

    public Vm get(String vmId) {
        return vmControlPort.get(vmId);
    }

    public Vm create(CreateVmCommand command) {
        VmTemplate template = templateCatalog.find(command.templateId(), command.templateVersion())
                .orElseThrow(() -> new ApiException(
                        HttpStatus.NOT_FOUND, "TEMPLATE_NOT_FOUND", "존재하지 않는 템플릿입니다."));
        if (command.memoryMib() < template.minMemoryMib()) {
            throw new ApiException(
                    HttpStatus.UNPROCESSABLE_ENTITY,
                    "VALIDATION_ERROR",
                    "memory_mib는 이 템플릿의 최소값(" + template.minMemoryMib() + ") 이상이어야 합니다.");
        }
        return vmControlPort.create(UUID.randomUUID().toString(), command);
    }

    public Vm start(String vmId) {
        return vmControlPort.start(vmId);
    }

    public Vm stop(String vmId) {
        return vmControlPort.stop(vmId);
    }

    public void destroy(String vmId) {
        vmControlPort.destroy(vmId);
    }
}
