/* SPDX-License-Identifier: Apache-2.0 */
/* Zephyr adapter: on-demand snapshots and short, separately locked loop hooks.
 * No transport, health decision, control output or persistent crash storage. */
#ifndef XENOVA_DIAGNOSTICS_H
#define XENOVA_DIAGNOSTICS_H
#include <xenova/diagnostics_core.h>
#include <zephyr/kernel.h>
#include <xenova/resources.h>

struct xdiag_network {
	int rc;
	bool link_up;
	uint64_t tx, rx, tx_drop, rx_drop, pool_busy, protocol_errors;
	uint64_t tx_bytes, rx_bytes, counter_epoch;
	int bytes_rc;
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
	struct xdr_snapshot resources;
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
/* Internal low-frequency owner / snapshot adapter; same registered provider. */
int xdiag_network_read(struct xdiag_network *);
int xdiag_clock_us(uint64_t *out);
/* Reads the single collector's cache; never advances a CPU baseline or scans stacks. */
/* One boot-lifetime app provider. It only tries owner metadata locks and never
 * scans payloads, allocates, blocks, logs, or calls transport/readiness paths. */
typedef void (*xdiag_app_memory_provider)(struct xdr_app_region out[XDR_APP_REGIONS],struct xdr_app_buffer detail[XDR_MANUAL_REGIONS],void *context);
int xdiag_app_memory_register(xdiag_app_memory_provider provider,void *context,unsigned count);
int xdiag_resources_get(struct xdr_snapshot *);
/* Hot-path optional evidence: never waits for the collector. Same age/error
 * rules as resources_get; failure is unavailable evidence, not app failure. */
int xdiag_resources_try_get(struct xdr_snapshot *);
bool xdiag_cpu_supported(void);
int xdiag_loop_init(struct xdiag_loop *, uint64_t period_us, uint64_t deadline_us);
int xdiag_loop_begin(struct xdiag_loop *);
int xdiag_loop_end(struct xdiag_loop *);
/* Observe one static-lifetime loop in the local snapshot; does not schedule it. */
int xdiag_loop_watch(struct xdiag_loop *);

struct shell;
/* xenova diag [threads]; called by the app's existing xenova command group. */
int xdiag_shell_cmd(const struct shell *, size_t argc, char **argv);
#endif
