/* SPDX-License-Identifier: Apache-2.0 */
/* Deterministic release/completion tests: production core, synthetic clock only. */
#include <xenova/diagnostics_core.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

static void jitter_and_progress(void)
{
	struct xdiag_timing t;
	assert(xdiag_timing_init(&t, 20000, 20000, 0) == 0);
	const uint64_t starts[] = {0, 20120, 40070, 61500, 80100};
	const int64_t jitter[] = {0, 120, -50, 1430, -1400};
	for (unsigned i = 0; i < 5; ++i) {
		assert(xdiag_timing_begin(&t, starts[i]) == 0);
		assert(t.loop_seq == i && t.in_flight);
		assert(t.last_jitter_us == jitter[i]);
		assert(xdiag_timing_end(&t, starts[i] + 100) == 0);
		assert(t.loop_seq == i + 1 && !t.in_flight);
	}
	assert(t.interval.count == 4 && t.elapsed.count == 5);
	assert(t.interval.min == 18600 && t.interval.max == 21430);
	assert(t.absolute_jitter.sum == 3000 && t.absolute_jitter.max == 1430);
	assert(t.lateness.max == 1500 && t.elapsed.max == 100);
	assert(t.deadline_miss_count == 0 && t.skipped_release_count == 0);
	uint64_t upper;
	assert(xdiag_percentile_upper(&t.absolute_jitter, 99, &upper) == 0 && upper == 2047);
}

static void overrun_and_skipped(void)
{
	struct xdiag_timing t;
	assert(xdiag_timing_init(&t, 20000, 20000, 0) == 0);
	assert(xdiag_timing_begin(&t, 0) == 0);
	assert(xdiag_timing_end(&t, 20000) == 0); /* Exactly on deadline. */
	assert(t.deadline_miss_count == 0 && t.skipped_release_count == 0);
	assert(xdiag_timing_begin(&t, 20100) == 0);
	assert(xdiag_timing_end(&t, 85000) == 0);
	assert(t.deadline_miss_count == 1 && t.skipped_release_count == 3);
	assert(t.next_release_us == 100000);
	assert(xdiag_timing_begin(&t, 100000) == 0);
	assert(xdiag_timing_end(&t, 100010) == 0);
	assert(t.interval.last == 79900 && t.absolute_jitter.last == 59900);
	/* A wakeup stall remains lateness, not an origin reset hiding the stall. */
	assert(xdiag_timing_begin(&t, 205000) == 0);
	assert(t.lateness.last == 85000 && t.loop_seq == 3);
	assert(xdiag_timing_end(&t, 205100) == 0);
	assert(t.deadline_miss_count == 2 && t.skipped_release_count == 7);
	assert(t.next_release_us == 220000);
}

static void invalid_calls_are_unchanged(void)
{
	struct xdiag_timing t, before;
	assert(xdiag_timing_init(&t, 7000, 5000, 100) == 0);
	before = t;
	assert(xdiag_timing_begin(&t, 99) == -EAGAIN);
	assert(!memcmp(&t, &before, sizeof(t)));
	assert(xdiag_timing_end(&t, 100) == -EINVAL);
	assert(!memcmp(&t, &before, sizeof(t)));
	assert(xdiag_timing_begin(&t, 100) == 0);
	before = t;
	assert(xdiag_timing_begin(&t, 101) == -EBUSY);
	assert(xdiag_timing_end(&t, 99) == -EINVAL);
	assert(!memcmp(&t, &before, sizeof(t)));
	assert(xdiag_timing_end(&t, 5101) == 0);
	assert(t.deadline_miss_count == 1 && t.loop_seq == 1);
	before = t;
	assert(xdiag_timing_begin(&t, 5000) == -EINVAL);
	assert(!memcmp(&t, &before, sizeof(t)));
	assert(xdiag_timing_init(&t, 0, 1, 0) == -EINVAL);
	assert(!memcmp(&t, &before, sizeof(t)));
	assert(xdiag_timing_init(&t, 1, 1, UINT64_MAX) == -EINVAL);
	assert(!memcmp(&t, &before, sizeof(t)));
}

static void bounds_and_histograms(void)
{
	struct xdiag_timing t;
	uint64_t upper = 123;
	assert(xdiag_timing_init(&t, 1, 1, 0) == 0);
	assert(xdiag_percentile_upper(&t.interval, 99, &upper) == -ENODATA && upper == 123);
	assert(xdiag_timing_begin(&t, 0) == 0);
	assert(xdiag_timing_end(&t, UINT64_C(1) << 40) == 0);
	assert(xdiag_percentile_upper(&t.elapsed, 99, &upper) == -ERANGE && upper == 123);
	t.elapsed.saturated = true;
	assert(xdiag_percentile_upper(&t.elapsed, 50, &upper) == -ERANGE);
	assert(xdiag_percentile_upper(&t.elapsed, 0, &upper) == -EINVAL);
	assert(xdiag_timing_init(&t, 100, 100, UINT64_MAX - 100) == 0);
	assert(xdiag_timing_begin(&t, UINT64_MAX - 100) == 0);
	assert(xdiag_timing_end(&t, UINT64_MAX) == 0);
	struct xdiag_timing before = t;
	assert(xdiag_timing_begin(&t, UINT64_MAX) == -ERANGE);
	assert(!memcmp(&t, &before, sizeof(t)));
}

static void error_and_boot_history(void)
{
	struct xdiag_history h = {0};
	for (unsigned i = 0; i < 20; ++i) {
		assert(xdiag_history_record(&h, "network", i % 2 ? -EIO : 0, i, true) == 0);
	}
	assert(h.boot_events == 20 && h.boot_count == 8 && h.boot[0].uptime_ms == 12);
	assert(h.error_count == 10 && h.error_entries == 8 && h.errors[0].uptime_ms == 5);
	assert(h.errors[7].code == -EIO && h.errors[7].uptime_ms == 19);
	struct xdiag_history before = h;
	assert(xdiag_history_record(&h, "", -EIO, 99, false) == -EINVAL);
	assert(xdiag_history_record(&h, "01234567890123456789012345678901xx", -EIO, 99, false) == -ENAMETOOLONG);
	assert(!memcmp(&h, &before, sizeof(h)));
}

int main(void)
{
	jitter_and_progress(); overrun_and_skipped(); invalid_calls_are_unchanged();
	bounds_and_histograms(); error_and_boot_history();
	printf("PASS: jitter, completion, arbitrary period, skips, deadlines, errors, bounds\n");
	return 0;
}
