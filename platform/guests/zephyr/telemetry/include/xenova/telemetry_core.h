/* SPDX-License-Identifier: Apache-2.0 */
/* Telemetry 2.0 wire model. No RTOS, sockets, allocation or health inference. */
#ifndef XENOVA_TELEMETRY_CORE_H
#define XENOVA_TELEMETRY_CORE_H
#include <xenova/diagnostics_core.h>

#define XT_PAYLOAD_MAX 4096
#define XT_EVENT_CAPACITY 32
#define XT_THREAD_MAX 32
#define XT_UUID_BYTES 37
#define XT_STREAMS 5
enum xt_kind { XT_INVENTORY, XT_HEARTBEAT, XT_MEMORY, XT_NETWORK,
	XT_PROGRESS, XT_INTERVAL, XT_JITTER, XT_LATENESS, XT_ELAPSED,
	XT_PUBLISHER, XT_EVENT, XT_THREADS, XT_HISTORY };

struct xt_identity {
	char vm_id[37], boot_id[37], publisher_id[37];
	/* Empty binding fields mean unbound; partial bindings are rejected. */
	char host_boot_id[37], execution_id[37], receipt_id[37];
};
struct xt_thread {
	char name[64], state[65];
	bool name_truncated;
	uint32_t flags;
	int priority, stack_rc;
	uint64_t stack_bytes, unused_bytes;
};
struct xt_observation {
	uint64_t start_ms, end_ms, elapsed_us, now_us, frequency_hz;
	int collection_rc, elapsed_rc, history_rc, heap_rc, network_rc, timing_rc, threads_rc;
	uint64_t heap_used, heap_free, heap_peak;
	uint64_t net[6];
	bool interface_up, carrier;
	struct xdiag_history history;
	struct xdiag_timing timing;
	size_t rows, rows_seen;
	struct xt_thread threads[XT_THREAD_MAX];
};
struct xt_inventory {
	const char *os_version, *build_id, *profile, *task_id, *interface_name;
	char mac[18], ipv4[16];
	bool diagnostics, timing, threads, heap, publisher;
};
struct xt_counters {
	uint64_t collection_failures, connection_attempts, submit_failures;
	uint64_t events_dropped, payload_oversize, puback_timeouts;
	uint64_t source_missed, live_discarded, snapshots_expired;
	uint64_t submitted, pubacked, serialized_us, send_us, collect_us;
	uint64_t last_success_ms;
	bool has_success, stopping;
};
struct xt_publisher {
	struct xt_identity identity;
	struct xt_inventory inventory;
	struct xt_counters counters;
	uint64_t sequence[XT_STREAMS], sample_sequence;
	char task_epoch[37];
	uint64_t task_origin;
	bool task_seen;
	struct xdiag_event events[XT_EVENT_CAPACITY];
	size_t event_count;
	uint64_t event_cursor;
};
struct xt_message {
	char topic[112], bytes[XT_PAYLOAD_MAX + 1];
	size_t length;
	unsigned stream, qos;
	bool retain;
};

bool xt_uuid_valid(const char *value);
/* IDs come from verified host injection or cryptographic entropy, never PRNG fallback. */
int xt_init(struct xt_publisher *, const struct xt_identity *, const struct xt_inventory *);
/* A session-local 128-bit namespace increment; exhaustion fails, never wraps. */
int xt_sample_id(struct xt_publisher *, char out[37]);
/* Capture stable Core events once. Source overflow and queue eviction are distinct. */
void xt_capture_events(struct xt_publisher *, const struct xdiag_history *);
void xt_event_acked(struct xt_publisher *);
/* Caller owns the publisher and frozen observation. Successful encoding commits
 * a stream seq. Keep the returned bytes unchanged for same-connection QoS1 retry.
 * snapshot_id is one frozen snapshot, part is zero-based. No partial JSON on error. */
int xt_encode(struct xt_publisher *, enum xt_kind, const struct xt_observation *,
	      uint64_t sent_ms, const char sample_id[37], const char snapshot_id[37],
	      unsigned part, struct xt_message *);
#endif
