/* SPDX-License-Identifier: Apache-2.0 */
/* Exact field mapping from the frozen Zephyr Diagnostics port, not console JSON. */
#include <xenova/telemetry.h>
#include <xenova/diagnostics.h>
#include <zephyr/net/net_if.h>
#include <errno.h>
#include <string.h>
#include "writer.h"
#if defined(CONFIG_XENOVA_TELEMETRY_TEST_OBSERVER)
void xt_test_observation(const struct xdiag_snapshot *,const struct xt_observation *);
#endif

int xt_observe(struct xt_observation *out,bool threads)
{
	/* Worker-only scratch. This function is not a second public concurrent collector. */
	static struct xdiag_snapshot source;
	memset(out,0,sizeof(*out));
	out->start_ms=k_uptime_get();
	int rc=xdiag_snapshot_get(&source,threads);
	out->end_ms=k_uptime_get();
	out->collection_rc=rc ? rc : source.collection_rc;
	out->history_rc=rc;
	out->elapsed_rc=rc ? rc : source.collection_rc;
	out->elapsed_us=source.collection_elapsed_us;
	if (rc) {
		out->heap_rc=out->network_rc=out->timing_rc=out->threads_rc=rc;
		return rc;
	}
	out->history=source.history;
	out->resources=source.resources;
	for(size_t i=0;i<out->resources.rows;i++){
	 struct xdr_thread *t=&out->resources.threads[i];
	 while(!xt_utf8_valid(t->name)&&t->name[0]){t->name[strlen(t->name)-1]=0;t->name_truncated=true;}
	}
	out->frequency_hz=source.cycle_hz;
	out->heap_rc=source.heap_rc;
	out->heap_used=source.heap_allocated_bytes;out->heap_free=source.heap_free_bytes;out->heap_peak=source.heap_peak_bytes;
	out->network_rc=source.network.rc;
	out->net[0]=source.network.tx;out->net[1]=source.network.rx;
	out->net[2]=source.network.tx_drop;out->net[3]=source.network.rx_drop;
	out->net[4]=source.network.pool_busy;out->net[5]=source.network.protocol_errors;
	struct net_if *iface=net_if_get_default();
	if (iface && !out->network_rc) { out->interface_up=net_if_is_up(iface);out->carrier=net_if_is_carrier_ok(iface); }
	out->timing_rc=source.timing_rc;out->timing=source.timing;
	/* Read after copying timing: never subtract collection-start from a newer completion. */
	if (xdiag_clock_us(&out->now_us) && !out->timing_rc) { out->timing_rc=-ENOTSUP; }
	out->threads_rc=source.threads_rc;out->rows=source.thread_count;out->rows_seen=source.threads_seen;
	for (size_t i=0;i<source.thread_count && i<XT_THREAD_MAX;++i) {
		struct xt_thread *t=&out->threads[i];struct xdiag_thread *s=&source.threads[i];
		memcpy(t->name,s->name,sizeof(s->name));memcpy(t->state,s->state,sizeof(s->state));
		t->name_truncated=s->name_truncated;t->flags=s->state_flags;t->priority=s->priority;
		/* Core has a byte-bounded OS name. A cut UTF-8 suffix is a reported
		 * truncation, never invalid JSON or a fabricated complete name. */
		while (!xt_utf8_valid(t->name) && t->name[0]) {
			t->name[strlen(t->name)-1]=0;t->name_truncated=true;
		}
		t->stack_rc=s->stack_rc;t->stack_bytes=s->stack_bytes;t->unused_bytes=s->unused_bytes;
	}
	out->end_ms=k_uptime_get();
#if defined(CONFIG_XENOVA_TELEMETRY_TEST_OBSERVER)
	xt_test_observation(&source,out);
#endif
	return 0;
}
