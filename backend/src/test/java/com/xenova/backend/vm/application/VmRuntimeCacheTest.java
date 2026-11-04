package com.xenova.backend.vm.application;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.xenova.backend.common.error.ApiException;
import com.xenova.backend.vm.domain.VmControlPort;
import com.xenova.backend.vm.domain.VmRuntime;
import com.xenova.backend.vm.domain.VmState;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;

class VmRuntimeCacheTest {

    private static final Instant START = Instant.parse("2026-11-02T08:00:00Z");
    private static final VmRuntime RUNNING = new VmRuntime("vm-1", VmState.RUNNING, 1, 256);
    private static final VmRuntime OTHER = new VmRuntime("vm-2", VmState.RUNNING, 1, 512);

    private final MutableClock clock = new MutableClock(START);
    private VmControlPort port;
    private VmRuntimeCache cache;

    @BeforeEach
    void setUp() {
        port = mock(VmControlPort.class);
        cache = new VmRuntimeCache(port, properties(true), clock);
    }

    @Test
    void freshSnapshotIsServedWithoutAskingDom0() {
        when(port.list()).thenReturn(List.of(RUNNING));

        cache.refresh();
        clock.advance(Duration.ofSeconds(9));
        VmRuntimeCache.Snapshot snapshot = cache.read();

        assertThat(snapshot.runtimes()).containsEntry("vm-1", RUNNING);
        assertThat(snapshot.observedAt()).isEqualTo(START);
        assertThat(cache.find("vm-1")).contains(RUNNING);
        assertThat(cache.find("vm-9")).isEmpty();
        verify(port, times(1)).list();
        verify(port, never()).find("vm-1");
    }

    @Test
    void readWithoutSnapshotOrPastTheStaleLimitAsksDom0Again() {
        when(port.list()).thenReturn(List.of(RUNNING));

        cache.read();
        clock.advance(Duration.ofSeconds(11));
        VmRuntimeCache.Snapshot snapshot = cache.read();

        assertThat(snapshot.observedAt()).isEqualTo(START.plusSeconds(11));
        verify(port, times(2)).list();
    }

    @Test
    void staleSnapshotWithFailingDom0IsAnErrorNotTheOldList() {
        when(port.list()).thenReturn(List.of(RUNNING))
                .thenThrow(new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "VM_CONTROL_UNAVAILABLE", "down"));

        cache.refresh();
        clock.advance(Duration.ofSeconds(11));

        assertThatThrownBy(cache::read).isInstanceOf(ApiException.class);
    }

    @Test
    void failedRefreshKeepsTheSnapshotUntilTheStaleLimit() {
        when(port.list()).thenReturn(List.of(RUNNING))
                .thenThrow(new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "VM_CONTROL_UNAVAILABLE", "down"));

        cache.refresh();
        clock.advance(Duration.ofSeconds(2));
        assertThatThrownBy(cache::refresh).isInstanceOf(ApiException.class);

        assertThat(cache.read().runtimes()).containsEntry("vm-1", RUNNING);
    }

    @Test
    void concurrentReadsOfAMissingSnapshotAskDom0Once() throws Exception {
        CountDownLatch inside = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        AtomicInteger calls = new AtomicInteger();
        when(port.list()).thenAnswer(invocation -> {
            calls.incrementAndGet();
            inside.countDown();
            release.await(5, TimeUnit.SECONDS);
            return List.of(RUNNING);
        });
        ExecutorService pool = Executors.newFixedThreadPool(10);
        try {
            List<Future<VmRuntimeCache.Snapshot>> results = new ArrayList<>();
            for (int i = 0; i < 10; i++) {
                results.add(pool.submit(cache::read));
            }
            assertThat(inside.await(5, TimeUnit.SECONDS)).isTrue();
            Thread.sleep(100);
            release.countDown();
            for (Future<VmRuntimeCache.Snapshot> result : results) {
                assertThat(result.get(5, TimeUnit.SECONDS).runtimes()).containsEntry("vm-1", RUNNING);
            }
        } finally {
            pool.shutdownNow();
        }
        assertThat(calls).hasValue(1);
    }

    @Test
    void stopAndDeleteRemoveTheVmAndLaunchPutsItWithoutAskingDom0() {
        when(port.list()).thenReturn(List.of(RUNNING, OTHER));
        cache.refresh();

        cache.remove("vm-1");
        assertThat(cache.read().runtimes()).doesNotContainKey("vm-1").containsKey("vm-2");

        cache.put("vm-1", RUNNING);
        assertThat(cache.read().runtimes()).containsEntry("vm-1", RUNNING);
        verify(port, times(1)).list();
    }

    @Test
    void operationDuringARefreshInFlightIsNotOverwrittenByThatOlderList() throws Exception {
        when(port.list()).thenReturn(List.of(RUNNING, OTHER));
        cache.refresh();
        CountDownLatch inside = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        // This list was read before the stop finished, so it still shows vm-1 running.
        when(port.list()).thenAnswer(invocation -> {
            inside.countDown();
            release.await(5, TimeUnit.SECONDS);
            return List.of(RUNNING, OTHER);
        });
        ExecutorService pool = Executors.newSingleThreadExecutor();
        try {
            Future<VmRuntimeCache.Snapshot> refresh = pool.submit(cache::refresh);
            assertThat(inside.await(5, TimeUnit.SECONDS)).isTrue();
            cache.remove("vm-1");
            release.countDown();
            assertThat(refresh.get(5, TimeUnit.SECONDS).runtimes()).doesNotContainKey("vm-1").containsKey("vm-2");
        } finally {
            pool.shutdownNow();
        }
        // The next list after the stop is trusted again.
        when(port.list()).thenReturn(List.of(OTHER));
        assertThat(cache.refresh().runtimes()).doesNotContainKey("vm-1");
    }

    @Test
    void launchReplyWithoutVmIdIsStoredUnderTheRequestedId() {
        when(port.list()).thenReturn(List.of(OTHER));
        cache.refresh();

        cache.put("vm-1", new VmRuntime(null, VmState.RUNNING, 1, 256));

        assertThat(cache.read().runtimes()).containsKey("vm-1").containsKey("vm-2");
    }

    @Test
    void disabledCacheAsksDom0EveryTime() {
        VmRuntimeCache disabled = new VmRuntimeCache(port, properties(false), clock);
        when(port.list()).thenReturn(List.of(RUNNING));
        when(port.find("vm-1")).thenReturn(Optional.of(RUNNING));

        disabled.read();
        disabled.read();
        disabled.remove("vm-1");

        assertThat(disabled.find("vm-1")).contains(RUNNING);
        verify(port, times(2)).list();
        verify(port, times(1)).find("vm-1");
    }

    private static VmRuntimeCacheProperties properties(boolean enabled) {
        return new VmRuntimeCacheProperties(enabled, Duration.ofSeconds(1), Duration.ofSeconds(10));
    }

    static final class MutableClock extends Clock {

        private volatile Instant now;

        MutableClock(Instant now) {
            this.now = now;
        }

        void advance(Duration duration) {
            now = now.plus(duration);
        }

        @Override
        public ZoneId getZone() {
            return ZoneOffset.UTC;
        }

        @Override
        public Clock withZone(ZoneId zone) {
            return this;
        }

        @Override
        public Instant instant() {
            return now;
        }
    }
}
