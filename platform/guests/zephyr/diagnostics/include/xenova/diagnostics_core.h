/* SPDX-License-Identifier: Apache-2.0 */
/* Transport/RTOS-independent bounded records. Caller serializes each object. */
#ifndef XENOVA_DIAGNOSTICS_CORE_H
#define XENOVA_DIAGNOSTICS_CORE_H
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define XDIAG_RECORDS 8
#define XDIAG_STAGE_BYTES 32
#define XDIAG_HIST_BUCKETS 32

struct xdiag_event {
	uint64_t uptime_ms;
	int code;
	char stage[XDIAG_STAGE_BYTES];
};

struct xdiag_history {
	uint64_t boot_events, error_count;
	size_t boot_count, error_entries;
	struct xdiag_event boot[XDIAG_RECORDS], errors[XDIAG_RECORDS];
};

/* Histograms: 0, 1, 2..3, 4..7, ...; last bucket >= 2^30 us. */
struct xdiag_metric {
	uint64_t count, last, min, max, sum;
	uint64_t histogram[XDIAG_HIST_BUCKETS];
	bool saturated;
};

struct xdiag_timing {
	uint64_t period_us, deadline_us, next_release_us;
	uint64_t release_us, start_us, previous_start_us, last_completion_us;
	uint64_t loop_seq, deadline_miss_count, skipped_release_count;
	int64_t last_jitter_us;
	bool initialized, in_flight, has_previous_start, saturated;
	struct xdiag_metric interval, absolute_jitter, lateness, elapsed;
};

/* Nonzero code also records an error. Full rings retain the newest 8 events. */
int xdiag_history_record(struct xdiag_history *, const char *stage, int code,
			 uint64_t uptime_ms, bool boot_event);

/* Arbitrary positive period/deadline, in local monotonic microseconds.
 * begin refuses early, nested or backward calls. end alone advances loop_seq.
 * Schedule remains anchored to origin; releases strictly before completion are
 * skipped and counted, never replayed in a burst. end == deadline is on time.
 * Errors leave the object unchanged. No allocation, logging or I/O occurs. */
int xdiag_timing_init(struct xdiag_timing *, uint64_t period_us,
		      uint64_t deadline_us, uint64_t origin_us);
int xdiag_timing_begin(struct xdiag_timing *, uint64_t now_us);
int xdiag_timing_end(struct xdiag_timing *, uint64_t now_us);

/* Returns a bucket upper bound, not an exact percentile. -ENODATA: no samples;
 * -ERANGE: top unbounded bucket/counter saturation; never manufactures a value. */
int xdiag_percentile_upper(const struct xdiag_metric *, unsigned percentile,
			   uint64_t *upper_us);
#endif
