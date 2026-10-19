package com.perseus.backend.event.application;

import com.perseus.backend.event.domain.ReportedVmState;
import com.perseus.backend.event.domain.ReportedVmStateRepository;
import com.perseus.backend.vm.domain.VmState;
import java.time.Clock;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

// Single place that decides when a VM state change becomes a vm.state.changed event.
// User operations and the periodic check both go through it, so one transition is recorded once.
@Service
public class VmStateTracker {

    public static final String CAUSE_USER_START = "user_start";
    public static final String CAUSE_USER_STOP = "user_stop";
    public static final String CAUSE_DETECTED = "detected";

    private final ReportedVmStateRepository repository;
    private final EventRecorder eventRecorder;
    private final Clock clock;
    // VMs with a start/stop/delete in flight, and the sequence number of each VM's last operation
    // begin or end. The periodic check skips a VM that is busy or was touched after its list was read,
    // so an intermediate or already outdated state is not reported as a detected change.
    // A counter is used instead of the clock so equal timestamps cannot hide an operation.
    private final Set<String> busy = ConcurrentHashMap.newKeySet();
    private final Map<String, Long> lastTouched = new ConcurrentHashMap<>();
    private final AtomicLong sequence = new AtomicLong();

    public VmStateTracker(ReportedVmStateRepository repository, EventRecorder eventRecorder, Clock clock) {
        this.repository = repository;
        this.eventRecorder = eventRecorder;
        this.clock = clock;
    }

    public void beginOperation(String vmId) {
        busy.add(vmId);
        lastTouched.put(vmId, sequence.incrementAndGet());
    }

    public void endOperation(String vmId) {
        lastTouched.put(vmId, sequence.incrementAndGet());
        busy.remove(vmId);
    }

    // Taken just before the VM list is read.
    public long mark() {
        return sequence.get();
    }

    // True when a state read after mark() may not reflect the VM any more.
    public boolean touchedSince(String vmId, long mark) {
        if (busy.contains(vmId)) {
            return true;
        }
        Long touched = lastTouched.get(vmId);
        return touched != null && touched > mark;
    }

    // A user operation changed the state; it is always recorded with the operation's cause.
    @Transactional
    public void changedByUser(String vmId, VmState from, VmState to, String cause) {
        remember(vmId, to);
        eventRecorder.vmStateChanged(vmId, from, to, cause);
    }

    // The state is known without a transition worth reporting, e.g. right after creation.
    @Transactional
    public void remember(String vmId, VmState state) {
        ReportedVmState reported = repository.findById(vmId).orElse(null);
        if (reported == null) {
            repository.save(new ReportedVmState(vmId, state, clock.instant()));
        } else if (reported.getState() != state) {
            reported.change(state, clock.instant());
            repository.save(reported);
        }
    }

    // The periodic check saw this state. The first sighting only sets the baseline, because
    // the previous state is unknown (e.g. after a server restart).
    @Transactional
    public void observed(String vmId, VmState state) {
        ReportedVmState reported = repository.findById(vmId).orElse(null);
        if (reported == null) {
            repository.save(new ReportedVmState(vmId, state, clock.instant()));
            return;
        }
        VmState previous = reported.getState();
        if (previous == state) {
            return;
        }
        reported.change(state, clock.instant());
        repository.save(reported);
        eventRecorder.vmStateChanged(vmId, previous, state, CAUSE_DETECTED);
    }

    @Transactional
    public void forget(String vmId) {
        repository.findById(vmId).ifPresent(repository::delete);
    }
}
