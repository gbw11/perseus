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
#define XDR_APP_REGIONS 8
#define XDR_AUTO_REGIONS 4
#define XDR_MANUAL_REGIONS 4
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
/* Regions are disjoint app owners. reserved includes metadata/workspace;
 * used/free/peak cover only the documented payload allocation units. */
struct xdr_app_region {
 int rc;
 uint64_t reserved,capacity,used,peak,exhausted,contention;
};
/* Small MANUAL owner metadata, never readiness or output permission. */
enum xdr_buffer_phase { XDR_BUFFER_EMPTY, XDR_BUFFER_STORED, XDR_BUFFER_PENDING,
 XDR_BUFFER_RUNNING, XDR_BUFFER_RETAINED };
enum xdr_input_status { XDR_INPUT_MISSING, XDR_INPUT_UNCLASSIFIED, XDR_INPUT_ACCEPTED,
 XDR_INPUT_INVALID, XDR_INPUT_EXPIRED, XDR_INPUT_CONTEXT_CHANGED };
struct xdr_app_buffer {
 uint64_t content_bytes,sequence,lifetime,context_revision,connection_revision,read_sequence;
 int64_t source_ms;
 int source_rc;
 uint8_t phase,input_status;
 bool performed,revoked;
};
struct xdr_snapshot {
 int firmware_rc,page_rc;
 uint64_t ram_region_bytes,mapped_bytes,static_data_bytes,page_free_bytes;
 bool app_registered;
 uint8_t app_count;
 struct xdr_app_region app[XDR_APP_REGIONS];
 struct xdr_app_buffer app_buffer[XDR_MANUAL_REGIONS];
	int heap_rc;
	uint64_t heap_allocated_bytes, heap_free_bytes;
	uint64_t collect_us,total_collect_us,max_collect_us;
	uint64_t net_start_ms,net_end_ms,tx_bytes,rx_bytes,tx_bytes_per_s,rx_bytes_per_s,net_epoch;
	int net_bytes_rc, net_rate_rc;
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
