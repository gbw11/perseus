package com.xenova.backend.metrics.application;

import com.xenova.backend.agent.domain.MetricSample;
import com.xenova.backend.agent.domain.MetricSampleData.CpuWindow;
import com.xenova.backend.agent.domain.MetricValue;
import com.xenova.backend.common.UnsignedDecimal;
import java.time.Instant;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.function.Predicate;

/** Latest observed CPU window for one VM. A partial or failed observation stays visible as such. */
public record ZephyrThreads(
        String vmId, String bootId, String publisherInstanceId, String sampleSeq,
        String startUptimeMs, String endUptimeMs, Integer windowMs, Instant receivedAt, boolean stale,
        String threadsStatus, Integer rowsSeen, Integer rowsReturned, int partsReceived, Integer partCount,
        boolean complete, boolean conflictingPages, List<ThreadRow> items) {

    static ZephyrThreads latest(String vmId, List<MetricSample> samples, Predicate<Instant> stale) {
        Map<Run, Instant> firstSeen = new HashMap<>();
        List<MetricSample> pages = samples.stream().filter(sample -> sample.getCpuWindow() != null).toList();
        for (MetricSample page : pages) {
            firstSeen.merge(Run.of(page), page.getReceivedAt(), (a, b) -> a.isBefore(b) ? a : b);
        }
        // Use each run's first observation in the query window so a delayed page cannot replace a known newer run.
        Run run = firstSeen.keySet().stream().max(Comparator.comparing((Run candidate) -> firstSeen.get(candidate))
                .thenComparing(Run::bootId).thenComparing(Run::publisher)).orElse(null);
        if (run == null) {
            return new ZephyrThreads(vmId, null, null, null, null, null, null, null, true,
                    null, null, null, 0, null, false, false, List.of());
        }
        List<MetricSample> runPages = pages.stream().filter(page -> run.equals(Run.of(page))).toList();
        String seq = runPages.stream().map(page -> page.getCpuWindow().sampleSeq())
                .max(UnsignedDecimal::compare).orElseThrow();
        List<MetricSample> windowPages = runPages.stream()
                .filter(page -> seq.equals(page.getCpuWindow().sampleSeq()))
                .sorted(Comparator.comparing(MetricSample::getReceivedAt)).toList();
        MetricSample first = windowPages.getFirst();
        CpuWindow window = first.getCpuWindow();
        Map<Integer, List<ThreadRow>> parts = new LinkedHashMap<>();
        Map<String, ThreadRow> rows = new LinkedHashMap<>();
        boolean conflicting = false;
        for (MetricSample page : windowPages) {
            CpuWindow pageWindow = page.getCpuWindow();
            if (!sameWindow(window, pageWindow)) {
                conflicting = true;
                continue;
            }
            List<ThreadRow> part = page.getValues().stream().filter(value -> "cpu.thread.percent".equals(value.name()))
                    .map(ThreadRow::of).toList();
            List<ThreadRow> previous = parts.putIfAbsent(pageWindow.partIndex(), part);
            if (previous != null) {
                conflicting |= !previous.equals(part);
                continue;
            }
            for (ThreadRow row : part) {
                conflicting |= rows.putIfAbsent(row.threadId(), row) != null;
            }
        }
        List<ThreadRow> items = conflicting ? List.of() : rows.values().stream()
                .sorted(Comparator.comparing(ThreadRow::cpuPercent, Comparator.nullsLast(Comparator.reverseOrder()))
                        .thenComparing(ThreadRow::threadId, UnsignedDecimal::compare)).toList();
        return new ZephyrThreads(vmId, run.bootId, run.publisher, seq, window.startUptimeMs(), window.endUptimeMs(),
                first.getWindowMs(), first.getReceivedAt(), stale.test(first.getReceivedAt()), window.threadsStatus(),
                window.rowsSeen(), window.rowsReturned(), parts.size(), window.partCount(),
                !conflicting && parts.size() == window.partCount(), conflicting, items);
    }

    private static boolean sameWindow(CpuWindow first, CpuWindow page) {
        return first.startUptimeMs().equals(page.startUptimeMs()) && first.endUptimeMs().equals(page.endUptimeMs())
                && first.partCount() == page.partCount() && first.rowsReturned() == page.rowsReturned()
                && Objects.equals(first.rowsSeen(), page.rowsSeen())
                && first.threadsStatus().equals(page.threadsStatus());
    }

    public record ThreadRow(String threadId, String threadName, boolean nameTruncated,
                            Double cpuPercent, String unavailableReason) {
        private static ThreadRow of(MetricValue value) {
            return new ThreadRow(value.labels().get("thread_id"), value.labels().get("thread_name"),
                    "true".equals(value.labels().get("name_truncated")), value.value(), value.unavailableReason());
        }
    }

    private record Run(String bootId, String publisher) {
        static Run of(MetricSample page) {
            return new Run(page.getBootId(), page.getCpuWindow().publisherInstanceId());
        }
    }
}
