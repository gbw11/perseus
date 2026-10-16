package com.perseus.backend.report.performance.application;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.stream.LongStream;
import org.junit.jupiter.api.Test;

class DurationStatisticsTest {

    @Test
    void medianUsesMiddleValueOrAverageOfTwoMiddles() {
        assertThat(DurationStatistics.median(List.of(100L, 200L, 900L))).isEqualTo(200L);
        assertThat(DurationStatistics.median(List.of(100L, 200L, 300L, 900L))).isEqualTo(250L);
        assertThat(DurationStatistics.median(List.of())).isNull();
    }

    @Test
    void p95UsesNearestRank() {
        List<Long> hundred = LongStream.rangeClosed(1, 100).boxed().toList();
        assertThat(DurationStatistics.p95(hundred)).isEqualTo(95L);
        assertThat(DurationStatistics.p95(List.of(100L, 200L, 900L))).isEqualTo(900L);
        assertThat(DurationStatistics.p95(List.of())).isNull();
    }
}
