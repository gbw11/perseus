/* SPDX-License-Identifier: Apache-2.0 */
/* Zephyr adapter: on-demand snapshots and short, separately locked loop hooks.
 * No transport, health decision, control output or persistent crash storage. */
#ifndef XENOVA_DIAGNOSTICS_H
#define XENOVA_DIAGNOSTICS_H
#include <xenova/diagnostics_core.h>
#include <zephyr/kernel.h>

struct xdiag_network {
	int rc;
	bool link_up;
	uint64_t tx, rx, tx_drop, rx_drop, pool_busy, protocol_errors;
};

/* Provider must be bounded and supply real observations; output is ignored on
 * nonzero return. Register once before use; callback/context must remain alive. */
typedef int (*xdiag_network_provider)(struct xdiag_network *, void *context);

struct xdiag_loop {
	struct k_spinlock lock;
	struct xdiag_timing timing;
};

struct xdiag_thread {
	char name[32], state[48];
	bool name_truncated;
	uint8_t state_flags;
	int priority, stack_rc;
	size_t stack_bytes, unused_bytes;
};

struct xdiag_snapshot {
	uint64_t captured_uptime_ms, collection_elapsed_us;
	uint64_t cycle_hz, cycle_now_us;
	int clock_rc, collection_rc, threads_rc, heap_rc, timing_rc;
	struct xdiag_history history;
	size_t thread_count, threads_seen;
	struct xdiag_thread threads[CONFIG_XENOVA_DIAGNOSTICS_MAX_THREADS];
	size_t heap_allocated_bytes, heap_free_bytes, heap_peak_bytes;
	struct xdiag_network network;
	struct xdiag_timing timing;
};

/* Thread context only. errno preserved; truncated sections are explicit.
 * include_threads scans stack fill patterns while the thread list is stable:
 * intrusive and not a hot-path API. The snapshot spans a collection interval,
 * not one atomic instant across all providers. No heap allocation is used. */
int xdiag_snapshot_get(struct xdiag_snapshot *, bool include_threads);
int xdiag_boot(const char *stage, int rc);
int xdiag_error(const char *stage, int rc);
int xdiag_network_register(xdiag_network_provider, void *context);
int xdiag_clock_us(uint64_t *out);
int xdiag_loop_init(struct xdiag_loop *, uint64_t period_us, uint64_t deadline_us);
int xdiag_loop_begin(struct xdiag_loop *);
int xdiag_loop_end(struct xdiag_loop *);
/* Observe one static-lifetime loop in the local snapshot; does not schedule it. */
int xdiag_loop_watch(struct xdiag_loop *);

struct shell;
/* xenova diag [threads]; called by the app's existing xenova command group. */
int xdiag_shell_cmd(const struct shell *, size_t argc, char **argv);
#endif
