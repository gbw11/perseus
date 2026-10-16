package com.perseus.backend.vm.domain;

import java.util.List;

public interface VmControlPort {

    List<Vm> list();

    Vm get(String vmId);

    Vm create(String vmId, CreateVmCommand command);

    Vm start(String vmId);

    Vm stop(String vmId);

    void destroy(String vmId);
}
