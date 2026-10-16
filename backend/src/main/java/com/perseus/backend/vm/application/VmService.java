package com.perseus.backend.vm.application;

import com.perseus.backend.vm.domain.CreateVmCommand;
import com.perseus.backend.vm.domain.Vm;
import com.perseus.backend.vm.domain.VmControlPort;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;

@Service
public class VmService {

    private final VmControlPort vmControlPort;

    public VmService(VmControlPort vmControlPort) {
        this.vmControlPort = vmControlPort;
    }

    public List<Vm> list() {
        return vmControlPort.list();
    }

    public Vm get(String vmId) {
        return vmControlPort.get(vmId);
    }

    public Vm create(CreateVmCommand command) {
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
