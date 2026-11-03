package com.xenova.backend.vm.application;

import com.xenova.backend.vm.domain.VmControlPort;
import com.xenova.backend.vm.domain.VmRuntime;
import java.time.Clock;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.locks.ReentrantLock;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.stereotype.Component;

// The last dom0-manager VM list. Every list call crosses Tailscale to the board and took 2.7-4.4s
// (S15P31E110-344), so screens read this snapshot and one background loop keeps it current.
// Disabled, every read goes to dom0-manager as before.
@Component
@EnableConfigurationProperties(VmRuntimeCacheProperties.class)
public class VmRuntimeCache {

    public record Snapshot(Map<String, VmRuntime> runtimes, Instant observedAt) {
    }

    private final VmControlPort vmControlPort;
    private final VmRuntimeCacheProperties properties;
    private final Clock clock;
    // One dom0-manager list at a time: readers that find the snapshot stale wait for the refresh in flight.
    private final ReentrantLock refreshLock = new ReentrantLock();
    private final Object writeLock = new Object();
    private volatile Snapshot snapshot;
    // A start/stop/delete result is newer than a list that was already in flight when it happened,
    // so such a VM keeps the operation's result when that list is stored.
    private final Map<String, Long> touched = new HashMap<>();
    private long sequence;

    public VmRuntimeCache(VmControlPort vmControlPort, VmRuntimeCacheProperties properties, Clock clock) {
        this.vmControlPort = vmControlPort;
        this.properties = properties;
        this.clock = clock;
    }

    public boolean enabled() {
        return properties.enabled();
    }

    // Fresh snapshot, or a new dom0-manager list when there is none or it is past the stale limit.
    // A failing refresh throws, so an outage is never hidden behind an old list.
    public Snapshot read() {
        if (!enabled()) {
            return fetch();
        }
        Snapshot current = snapshot;
        if (isFresh(current)) {
            return current;
        }
        refreshLock.lock();
        try {
            current = snapshot;
            if (isFresh(current)) {
                return current;
            }
            return store();
        } finally {
            refreshLock.unlock();
        }
    }

    // Always asks dom0-manager; used by the background loop.
    public Snapshot refresh() {
        if (!enabled()) {
            return fetch();
        }
        refreshLock.lock();
        try {
            return store();
        } finally {
            refreshLock.unlock();
        }
    }

    public Optional<VmRuntime> find(String vmId) {
        if (!enabled()) {
            return vmControlPort.find(vmId);
        }
        // A fresh full list answers for absent domains too: the VM is not in Xen.
        return Optional.ofNullable(read().runtimes().get(vmId));
    }

    // The operation's own result, e.g. the runtime returned by launch. Keyed by the id Spring asked for,
    // not by the response body, so a reply without vm_id cannot break the snapshot.
    public void put(String vmId, VmRuntime runtime) {
        update(vmId, runtime);
    }

    // The domain is gone after stop or delete.
    public void remove(String vmId) {
        update(vmId, null);
    }

    private void update(String vmId, VmRuntime runtime) {
        if (!enabled()) {
            return;
        }
        synchronized (writeLock) {
            touched.put(vmId, ++sequence);
            Snapshot current = snapshot;
            if (current == null) {
                return;
            }
            Map<String, VmRuntime> runtimes = new HashMap<>(current.runtimes());
            if (runtime == null) {
                runtimes.remove(vmId);
            } else {
                runtimes.put(vmId, runtime);
            }
            snapshot = new Snapshot(Map.copyOf(runtimes), current.observedAt());
        }
    }

    private Snapshot store() {
        long started;
        synchronized (writeLock) {
            started = sequence;
        }
        Snapshot fetched = fetch();
        synchronized (writeLock) {
            Map<String, VmRuntime> runtimes = new HashMap<>(fetched.runtimes());
            Snapshot previous = snapshot;
            touched.forEach((vmId, at) -> {
                if (at <= started) {
                    return;
                }
                VmRuntime kept = previous == null ? null : previous.runtimes().get(vmId);
                if (kept == null) {
                    runtimes.remove(vmId);
                } else {
                    runtimes.put(vmId, kept);
                }
            });
            touched.values().removeIf(at -> at <= started);
            snapshot = new Snapshot(Map.copyOf(runtimes), fetched.observedAt());
            return snapshot;
        }
    }

    private Snapshot fetch() {
        Instant observedAt = clock.instant();
        List<VmRuntime> runtimes = vmControlPort.list();
        Map<String, VmRuntime> byId = new HashMap<>();
        runtimes.stream()
                .filter(runtime -> runtime.vmId() != null)
                .forEach(runtime -> byId.putIfAbsent(runtime.vmId(), runtime));
        return new Snapshot(Map.copyOf(byId), observedAt);
    }

    private boolean isFresh(Snapshot current) {
        return current != null
                && !current.observedAt().plus(properties.staleLimit()).isBefore(clock.instant());
    }
}
