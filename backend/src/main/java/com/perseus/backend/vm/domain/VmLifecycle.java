package com.perseus.backend.vm.domain;

// Spring keeps retired VM specifications so a future restore can reuse the same vm_id and disk identity.
public enum VmLifecycle {
    REGISTERED,
    RETIRED
}
