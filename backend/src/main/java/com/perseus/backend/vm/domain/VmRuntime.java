package com.perseus.backend.vm.domain;

// What dom0-manager reports for a domain that exists in Xen. It knows nothing else about the VM.
public record VmRuntime(String vmId, VmState state, int vcpus, int memoryMib) {
}
