package com.xenova.backend.report.performance.application;

import java.util.List;

final class DurationStatistics {

    private DurationStatistics() {
    }

    // Average of the two middle values for an even count.
    static Long median(List<Long> sortedDurations) {
        int size = sortedDurations.size();
        if (size == 0) {
            return null;
        }
        if (size % 2 == 1) {
            return sortedDurations.get(size / 2);
        }
        return Math.round((sortedDurations.get(size / 2 - 1) + sortedDurations.get(size / 2)) / 2.0);
    }

    // Nearest-rank percentile: the smallest value that covers at least 95% of the samples.
    static Long p95(List<Long> sortedDurations) {
        int size = sortedDurations.size();
        if (size == 0) {
            return null;
        }
        int rank = (int) Math.ceil(0.95 * size);
        return sortedDurations.get(rank - 1);
    }
}
