package com.xenova.backend.vm.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;

import com.xenova.backend.event.application.VmStateWatcher;
import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class VmRuntimeRefresherTest {

    @Test
    void checksAgainAfterTheDelayAndKeepsGoingAfterAFailure() throws Exception {
        VmStateWatcher watcher = mock(VmStateWatcher.class);
        AtomicInteger calls = new AtomicInteger();
        CountDownLatch thirdCall = new CountDownLatch(3);
        doAnswer(invocation -> {
            thirdCall.countDown();
            if (calls.incrementAndGet() == 1) {
                throw new IllegalStateException("first check fails");
            }
            return null;
        }).when(watcher).check();
        VmRuntimeRefresher refresher = new VmRuntimeRefresher(watcher, properties(true, Duration.ofMillis(20)));

        refresher.start();
        try {
            assertThat(thirdCall.await(5, TimeUnit.SECONDS)).isTrue();
            assertThat(refresher.isRunning()).isTrue();
        } finally {
            refresher.stop();
        }

        assertThat(refresher.isRunning()).isFalse();
        int afterStop = calls.get();
        Thread.sleep(200);
        assertThat(calls.get()).isLessThanOrEqualTo(afterStop + 1);
    }

    @Test
    void waitsTheDelayBetweenChecks() throws Exception {
        VmStateWatcher watcher = mock(VmStateWatcher.class);
        AtomicInteger calls = new AtomicInteger();
        doAnswer(invocation -> {
            calls.incrementAndGet();
            return null;
        }).when(watcher).check();
        VmRuntimeRefresher refresher = new VmRuntimeRefresher(watcher, properties(true, Duration.ofMillis(300)));

        refresher.start();
        try {
            Thread.sleep(450);
        } finally {
            refresher.stop();
        }

        assertThat(calls.get()).isBetween(1, 2);
    }

    @Test
    void disabledCacheStartsNoLoop() throws Exception {
        VmStateWatcher watcher = mock(VmStateWatcher.class);
        VmRuntimeRefresher refresher = new VmRuntimeRefresher(watcher, properties(false, Duration.ofMillis(10)));

        refresher.start();
        Thread.sleep(100);

        assertThat(refresher.isRunning()).isFalse();
        verify(watcher, never()).check();
    }

    private static VmRuntimeCacheProperties properties(boolean enabled, Duration delay) {
        return new VmRuntimeCacheProperties(enabled, delay, Duration.ofSeconds(10));
    }
}
