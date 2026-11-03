/* SPDX-License-Identifier: Apache-2.0 */
/* Frozen Zephyr 4.4 port. Low-frequency collection never runs from loop hooks. */
#include <xenova/diagnostics.h>
#include <zephyr/init.h>
#include <zephyr/sys/sys_heap.h>
#include <errno.h>
#include <string.h>
#include <zephyr/sys/printk.h>

static struct xdiag_history history;
static struct xdiag_loop *watched;
static xdiag_network_provider network_provider;
static void *network_context;
K_MUTEX_DEFINE(diag_lock);

int xdiag_clock_us(uint64_t *out)
{
	if (!out) { return -EINVAL; }
#if defined(CONFIG_XENOVA_DIAGNOSTICS_TIMING)
	if (!sys_clock_hw_cycles_per_sec()) { return -EIO; }
	*out = k_cyc_to_us_floor64(k_cycle_get_64());
	return 0;
#else
	return -ENOTSUP;
#endif
}

static int event(const char *stage, int rc, bool boot)
{
	if (k_is_in_isr()) { return -EWOULDBLOCK; }
	if (k_mutex_lock(&diag_lock, K_MSEC(20))) { return -EBUSY; }
	int result = xdiag_history_record(&history, stage, rc, k_uptime_get(), boot);
	struct xdiag_event stored={0};
	if(!result&&rc)stored=history.errors[history.error_entries-1];
	k_mutex_unlock(&diag_lock);
	if(!result&&rc){
	 char hex[XDIAG_STAGE_BYTES*2]={0};
	 for(size_t i=0;stored.stage[i];i++){static const char digits[]="0123456789abcdef";unsigned c=(unsigned char)stored.stage[i];hex[i*2]=digits[c>>4];hex[i*2+1]=digits[c&15];}
	 printk("XDIAG_EVENT {\"seq\":%llu,\"uptime_ms\":%llu,\"code\":%d,\"stage_hex\":\"%s\",\"boot\":%s}\n",
	  stored.event_seq,stored.uptime_ms,stored.code,hex,boot?"true":"false");
	}
	return result;
}

int xdiag_boot(const char *stage, int rc) { return event(stage, rc, true); }
int xdiag_error(const char *stage, int rc)
{
	return rc ? event(stage, rc, false) : -EINVAL;
}

int xdiag_network_register(xdiag_network_provider provider, void *context)
{
	if (!provider || k_is_in_isr()) { return -EINVAL; }
	if (k_mutex_lock(&diag_lock, K_MSEC(20))) { return -EBUSY; }
	int rc = network_provider ? -EALREADY : 0;
	if (!rc) { network_provider = provider; network_context = context; }
	k_mutex_unlock(&diag_lock);
	return rc;
}
int xdiag_network_read(struct xdiag_network *out)
{
 if(k_mutex_lock(&diag_lock,K_MSEC(20)))return -EBUSY;
 xdiag_network_provider provider=network_provider;void *context=network_context;
 k_mutex_unlock(&diag_lock);return provider?provider(out,context):-ENOENT;
}

int xdiag_loop_init(struct xdiag_loop *loop, uint64_t period, uint64_t deadline)
{
	if (!loop) { return -EINVAL; }
	uint64_t now;
	int rc = xdiag_clock_us(&now);
	if (rc) { return rc; }
	/* Initialize before publishing the static-lifetime object to another thread. */
	memset(loop, 0, sizeof(*loop));
	return xdiag_timing_init(&loop->timing, period, deadline, now);
}

static int loop_event(struct xdiag_loop *loop, bool begin)
{
	if (!loop) { return -EINVAL; }
	k_spinlock_key_t key = k_spin_lock(&loop->lock);
	uint64_t now;
	int rc = xdiag_clock_us(&now);
	if (!rc) { rc = begin ? xdiag_timing_begin(&loop->timing, now) :
			       xdiag_timing_end(&loop->timing, now); }
	k_spin_unlock(&loop->lock, key);
	return rc;
}

int xdiag_loop_begin(struct xdiag_loop *loop) { return loop_event(loop, true); }
int xdiag_loop_end(struct xdiag_loop *loop) { return loop_event(loop, false); }

int xdiag_loop_watch(struct xdiag_loop *loop)
{
	if (!loop || !loop->timing.initialized || k_is_in_isr()) { return -EINVAL; }
	if (k_mutex_lock(&diag_lock, K_MSEC(20))) { return -EBUSY; }
	int rc = watched ? -EALREADY : 0;
	if (!rc) { watched = loop; }
	k_mutex_unlock(&diag_lock);
	return rc;
}

#if defined(CONFIG_XENOVA_DIAGNOSTICS_THREADS)
static void thread_row(const struct k_thread *thread, void *context)
{
	struct xdiag_snapshot *s = context;
	++s->threads_seen;
	if (s->thread_count == ARRAY_SIZE(s->threads)) {
		s->threads_rc = -ENOSPC;
		return;
	}
	struct xdiag_thread *row = &s->threads[s->thread_count++];
	const char *name = k_thread_name_get((k_tid_t)thread);
	row->name_truncated = name && strlen(name) >= sizeof(row->name);
	strncpy(row->name, name && *name ? name : "unnamed", sizeof(row->name) - 1);
	(void)k_thread_state_str((k_tid_t)thread, row->state, sizeof(row->state));
	row->state_flags = thread->base.thread_state;
	row->priority = k_thread_priority_get((k_tid_t)thread);
	row->stack_bytes = thread->stack_info.size;
	if (row->stack_bytes > CONFIG_XENOVA_DIAGNOSTICS_STACK_SCAN_LIMIT) {
		row->stack_rc = -E2BIG;
	} else {
		row->stack_rc = k_thread_stack_space_get(thread, &row->unused_bytes);
		if (!row->stack_rc && row->unused_bytes > row->stack_bytes) {
			row->stack_rc = -EIO;
		}
	}
}
#endif

static void heap_snapshot(struct xdiag_snapshot *s)
{
#if defined(CONFIG_XENOVA_DIAGNOSTICS_HEAP)
	/* Zephyr 4.4's own kernel shell uses _system_heap.heap. This is a pinned
	 * port dependency, NOT a public k_heap member API. Match k_malloc's lock
	 * while reading sys_heap stats; never sum this with overlapping pools. */
	extern struct k_heap _system_heap;
	struct sys_memory_stats stats;
	k_spinlock_key_t key = k_spin_lock(&_system_heap.lock);
	s->heap_rc = sys_heap_runtime_stats_get(&_system_heap.heap, &stats);
	k_spin_unlock(&_system_heap.lock, key);
	if (!s->heap_rc) {
		s->heap_allocated_bytes = stats.allocated_bytes;
		s->heap_free_bytes = stats.free_bytes;
		s->heap_peak_bytes = stats.max_allocated_bytes;
	}
#else
	s->heap_rc = -ENOTSUP;
#endif
}

int xdiag_snapshot_get(struct xdiag_snapshot *s, bool include_threads)
{
	if (!s) { return -EINVAL; }
	if (k_is_in_isr()) { return -EWOULDBLOCK; }
	memset(s, 0, sizeof(*s));
	int resources_rc=xdiag_resources_get(&s->resources);
	if (resources_rc) {
		s->resources.cpu.rc=s->resources.threads_rc=resources_rc;
		s->resources.net_bytes_rc=s->resources.net_rate_rc=resources_rc;
	}
	s->captured_uptime_ms = k_uptime_get();
	uint64_t start = 0, end;
	s->clock_rc = xdiag_clock_us(&start);
	if (!s->clock_rc) {
		s->cycle_hz = sys_clock_hw_cycles_per_sec();
		s->cycle_now_us = start;
	}
	if (k_mutex_lock(&diag_lock, K_MSEC(20))) { return -EBUSY; }
	s->history = history;
	xdiag_network_provider provider = network_provider;
	void *context = network_context;
	struct xdiag_loop *loop = watched;
	k_mutex_unlock(&diag_lock);
	s->timing_rc = -ENOENT;
	if (loop) {
		k_spinlock_key_t key = k_spin_lock(&loop->lock);
		s->timing = loop->timing;
		k_spin_unlock(&loop->lock, key);
		s->timing_rc = 0;
	}
	heap_snapshot(s);
	s->threads_rc = include_threads ? -ENOTSUP : -EAGAIN;
#if defined(CONFIG_XENOVA_DIAGNOSTICS_THREADS)
	if (include_threads) {
		s->threads_rc = 0;
		/* Stable thread lifetimes, bounded row/stack limits. This deliberately
		 * trades interrupt latency for correctness on an explicit request. */
		k_thread_foreach(thread_row, s);
	}
#endif
	s->network.rc = provider ? provider(&s->network, context) : -ENOENT;
	s->collection_rc = s->clock_rc ? s->clock_rc : xdiag_clock_us(&end);
	if (!s->collection_rc) {
		if (end < start) { s->collection_rc = -EIO; }
		else { s->collection_elapsed_us = end - start; }
	}
	return 0;
}

static int diagnostics_init(void)
{
	uint64_t now;
	int rc = xdiag_clock_us(&now);
	(void)xdiag_boot("diagnostics-ready", rc == -ENOTSUP ? 0 : rc);
	return 0;
}
SYS_INIT(diagnostics_init, APPLICATION, CONFIG_KERNEL_INIT_PRIORITY_DEFAULT);
