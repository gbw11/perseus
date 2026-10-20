package com.xenova.backend.event.application;

import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.vm.application.VmService;
import com.xenova.backend.vm.domain.Vm;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

// Xen does not tell anyone when a VM crashes or shuts itself down, so the VM list is polled and
// state changes that no user operation explains are recorded as vm.state.changed (cause detected).
@Component
public class VmStateWatcher {

    private static final Logger log = LoggerFactory.getLogger(VmStateWatcher.class);

    private final VmService vmService;
    private final VmStateTracker tracker;

    public VmStateWatcher(VmService vmService, VmStateTracker tracker) {
        this.vmService = vmService;
        this.tracker = tracker;
    }

    @Scheduled(
            initialDelayString = "${xenova.event.vm-state-check-interval-ms:5000}",
            fixedDelayString = "${xenova.event.vm-state-check-interval-ms:5000}")
    public void check() {
        long mark = tracker.mark();
        List<Vm> vms;
        try {
            vms = vmService.list();
        } catch (ApiException exception) {
            // Without dom0-manager the real state is unknown; reporting every VM as stopped would be wrong.
            log.debug("VM state check skipped: {}", exception.getMessage());
            return;
        }
        for (Vm vm : vms) {
            if (!tracker.touchedSince(vm.vmId(), mark)) {
                tracker.observed(vm.vmId(), vm.state());
            }
        }
    }
}
