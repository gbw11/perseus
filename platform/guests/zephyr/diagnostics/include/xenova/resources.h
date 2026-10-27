/* SPDX-License-Identifier: Apache-2.0 */
/* Guest-accounted UP runtime windows. Raw evidence stays local; no allocator,
 * transport, authority or scheduling decision belongs to this model. */
#ifndef XENOVA_RESOURCES_H
#define XENOVA_RESOURCES_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#define XDR_THREADS 32
#define XDR_REGIONS 2
struct xdr_counter { uint64_t total, busy, at_ms; };
struct xdr_window {
 uint64_t start_ms, end_ms, total_delta, busy_delta;
 int rc;
 uint32_t percent_milli; /* 100% = 100000, rounded half up */
};
struct xdr_thread {
 uint64_t lifetime, accounted, previous;
 char name[32];
 bool name_truncated;
 int rc;
 uint32_t percent_milli;
};
struct xdr_region {
 char id[24];
 int rc;
 uint64_t capacity, used, free;
};
struct xdr_snapshot {
	int heap_rc;
	uint64_t heap_allocated_bytes, heap_free_bytes;
	uint64_t collect_us,total_collect_us,max_collect_us;
	uint64_t net_start_ms,net_end_ms,tx_bytes,rx_bytes,tx_bytes_per_s,rx_bytes_per_s,net_epoch;
	int net_rate_rc;
 struct xdr_counter raw;
 struct xdr_window cpu;
 uint64_t sample;
 size_t rows, seen;
 int threads_rc;
 struct xdr_thread threads[XDR_THREADS];
 size_t regions;
 struct xdr_region memory[XDR_REGIONS];
};
int xdr_percent(uint64_t numerator, uint64_t denominator, uint32_t *out);
/* max_gap derives from the actual finite-width accounting clock, not cadence. */
void xdr_window(const struct xdr_counter *previous, const struct xdr_counter *now,
                uint64_t max_gap_ms, struct xdr_window *out);
#endif
