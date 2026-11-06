package com.xenova.backend.vm.application;

import com.xenova.backend.event.application.VmStateWatcher;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.SmartLifecycle;
import org.springframework.stereotype.Component;

// Keeps the runtime cache current: one VM state check (a dom0-manager list) right after another,
// with a short pause between. It runs on its own thread because a list can take seconds and must not
// hold up the shared @Scheduled thread (the manual input repeat stalled that way, 99f3c3a).
@Component
public class VmRuntimeRefresher implements SmartLifecycle {

    private static final Logger log = LoggerFactory.getLogger(VmRuntimeRefresher.class);

    private final VmStateWatcher watcher;
    private final VmRuntimeCacheProperties properties;
    private volatile Thread thread;
    private volatile boolean running;

    public VmRuntimeRefresher(VmStateWatcher watcher, VmRuntimeCacheProperties properties) {
        this.watcher = watcher;
        this.properties = properties;
    }

    @Override
    public synchronized void start() {
        if (running || !properties.enabled()) {
            return;
        }
        running = true;
        thread = new Thread(this::loop, "vm-runtime-refresher");
        thread.setDaemon(true);
        thread.start();
    }

    @Override
    public synchronized void stop() {
        running = false;
        Thread current = thread;
        thread = null;
        if (current != null) {
            current.interrupt();
        }
    }

    @Override
    public boolean isRunning() {
        return running;
    }

    private void loop() {
        while (running) {
            try {
                watcher.check();
            } catch (RuntimeException exception) {
                // check() already skips an unreachable dom0-manager; anything else must not end the loop.
                log.warn("VM runtime refresh failed", exception);
            }
            try {
                Thread.sleep(properties.refreshDelay().toMillis());
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                return;
            }
        }
    }
}
