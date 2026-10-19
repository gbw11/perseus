package com.perseus.backend.vm.domain;

import java.util.List;
import java.util.Optional;

// dom0-manager is stateless: it only sees domains that currently exist in Xen.
public interface VmControlPort {

    List<VmRuntime> list();

    // Empty when the domain does not exist in Xen (the VM is off or was never launched).
    Optional<VmRuntime> find(String vmId);

    // Creates the domain, reusing the disk of this vm_id if it exists. There is no separate start.
    VmRuntime launch(String vmId, VmLaunchSpec spec);

    // Graceful shutdown; the domain disappears and the disk is kept. Idempotent.
    void stop(String vmId);

    // Forced shutdown and disk deletion. Idempotent.
    void destroy(String vmId);
}
