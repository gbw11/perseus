/* SPDX-License-Identifier: Apache-2.0 */
/* Local JSON is versioned diagnostic output, not the MQTT inventory contract. */
#include <xenova/diagnostics.h>
#include <zephyr/shell/shell.h>
#include <errno.h>
#include <inttypes.h>
#include <string.h>

static struct xdiag_snapshot snapshot;
K_MUTEX_DEFINE(output_lock);
#define emit(...) shell_fprintf(sh, SHELL_NORMAL, __VA_ARGS__)

static void text(const struct shell *sh, const char *s)
{
	emit("\"");
	for (const unsigned char *p = (const unsigned char *)s; *p; ++p) {
		if (*p == '"' || *p == '\\') { emit("\\%c", *p); }
		else if (*p < 0x20) { emit("\\u%04x", *p); }
		else if (*p >= 0x80) {
			/* Preserve UTF-8 labels instead of treating each byte as Latin-1.
			 * Invalid/truncated bytes still produce valid JSON byte escapes. */
			unsigned count = *p >= 0xc2 && *p <= 0xdf ? 2 :
				*p >= 0xe0 && *p <= 0xef ? 3 : *p >= 0xf0 && *p <= 0xf4 ? 4 : 0;
			uint32_t cp = count ? *p & ((1U << (7 - count)) - 1) : 0;
			bool valid = count != 0;
			for (unsigned i = 1; valid && i < count; ++i) {
				if (!p[i] || (p[i] & 0xc0) != 0x80) { valid = false; }
				else { cp = (cp << 6) | (p[i] & 0x3f); }
			}
			valid = valid && cp >= (count == 2 ? 0x80U : count == 3 ? 0x800U : 0x10000U) &&
				cp <= 0x10ffff && !(cp >= 0xd800 && cp <= 0xdfff);
			if (!valid) { emit("\\u%04x", *p); }
			else {
				if (cp <= 0xffff) { emit("\\u%04x", (unsigned)cp); }
				else { cp -= 0x10000; emit("\\u%04x\\u%04x",
					(unsigned)(0xd800 + (cp >> 10)), (unsigned)(0xdc00 + (cp & 0x3ff))); }
				p += count - 1;
			}
		}
		else { emit("%c", *p); }
	}
	emit("\"");
}

static void status(const struct shell *sh, int rc)
{
	const char *reason = rc == 0 ? "ok" : rc == -ENOTSUP ? "unsupported" :
		rc == -ENOENT ? "not_registered" : rc == -EAGAIN ? "not_ready" :
		rc == -ENOSPC ? "truncated" : rc == -E2BIG ? "scan_limit" : "collection_failed";
	emit("\"status\":\"%s\",\"rc\":%d", reason, rc);
}

static void events(const struct shell *sh, const struct xdiag_event *e, size_t count)
{
	emit("[");
	for (size_t i = 0; i < count; ++i) {
		emit("%s{\"stage\":", i ? "," : ""); text(sh, e[i].stage);
		emit(",\"code\":%d,\"uptime_ms\":%" PRIu64 "}", e[i].code, e[i].uptime_ms);
	}
	emit("]");
}

static void metric(const struct shell *sh, const char *name, const struct xdiag_metric *m)
{
	emit(",\"%s\":{\"count\":%" PRIu64 ",\"saturated\":%s,\"value\":", name,
	     m->count, m->saturated ? "true" : "false");
	if (!m->count) { emit("null}"); return; }
	emit("{\"last_us\":%" PRIu64 ",\"min_us\":%" PRIu64 ",\"max_us\":%" PRIu64,
	     m->last, m->min, m->max);
	if (m->saturated) { emit(",\"mean_us\":null"); }
	else { emit(",\"mean_us\":%" PRIu64, m->sum / m->count); }
	for (unsigned p = 95; p <= 99; p += 4) {
		uint64_t upper;
		emit(",\"p%u_upper_us\":", p);
		if (xdiag_percentile_upper(m, p, &upper)) { emit("null"); }
		else { emit("%" PRIu64, upper); }
	}
	emit("}}");
}

int xdiag_shell_cmd(const struct shell *sh, size_t argc, char **argv)
{
	if (argc > 2 || (argc == 2 && strcmp(argv[1], "threads"))) { return -EINVAL; }
	if (k_mutex_lock(&output_lock, K_NO_WAIT)) { return -EBUSY; }
	int rc = xdiag_snapshot_get(&snapshot, argc == 2);
	if (rc) {
		shell_print(sh, "{\"schema_version\":1,\"ok\":false,\"rc\":%d}", rc);
		k_mutex_unlock(&output_lock); return rc;
	}
	const struct xdiag_snapshot *s = &snapshot;
	emit("{\"schema_version\":1,\"ok\":true,\"uptime_ms\":%" PRIu64 ",\"clock\":{",
	     s->captured_uptime_ms); status(sh, s->clock_rc);
	if (s->clock_rc) { emit(",\"frequency_hz\":null}"); }
	else { emit(",\"frequency_hz\":%" PRIu64 "}", s->cycle_hz); }
	emit(",\"collection\":{"); status(sh, s->collection_rc);
	if (s->collection_rc) { emit(",\"elapsed_us\":null}"); }
	else { emit(",\"elapsed_us\":%" PRIu64 "}", s->collection_elapsed_us); }
	emit(",\"boot_events_total\":%" PRIu64 ",\"boot\":", s->history.boot_events);
	events(sh, s->history.boot, s->history.boot_count);
	emit(",\"error_count\":%" PRIu64 ",\"errors\":", s->history.error_count);
	events(sh, s->history.errors, s->history.error_entries);
	emit(",\"heap\":{"); status(sh, s->heap_rc);
	if (s->heap_rc) { emit(",\"value\":null}"); }
	else { emit(",\"scope\":\"kernel_system_heap\",\"value\":{\"allocated_bytes\":%zu,"
		    "\"free_bytes\":%zu,\"peak_bytes\":%zu}}", s->heap_allocated_bytes,
		    s->heap_free_bytes, s->heap_peak_bytes); }
	emit(",\"threads\":{");
	if (s->threads_rc == -EAGAIN) { emit("\"status\":\"not_requested\",\"rc\":%d", s->threads_rc); }
	else { status(sh, s->threads_rc); }
	bool have_threads = !s->threads_rc || s->threads_rc == -ENOSPC;
	if (have_threads) { emit(",\"seen\":%zu,\"rows\":[", s->threads_seen); }
	else { emit(",\"seen\":null,\"rows\":null"); }
	for (size_t i = 0; i < s->thread_count; ++i) {
		const struct xdiag_thread *t = &s->threads[i];
		emit("%s{\"name\":", i ? "," : ""); text(sh, t->name);
		emit(",\"name_truncated\":%s,\"state_flags\":%u", t->name_truncated ? "true" : "false",
		     (unsigned)t->state_flags);
		emit(",\"state\":"); text(sh, t->state);
		emit(",\"priority\":%d,\"stack\":{", t->priority); status(sh, t->stack_rc);
		if (t->stack_rc) { emit(",\"value\":null}}"); }
		else { emit(",\"value\":{\"size_bytes\":%zu,\"unused_bytes\":%zu,\"used_bytes\":%zu}}}",
			t->stack_bytes, t->unused_bytes, t->stack_bytes - t->unused_bytes); }
	}
	if (have_threads) { emit("]"); }
	emit("},\"network\":{"); status(sh, s->network.rc);
	if (s->network.rc) { emit(",\"value\":null}"); }
	else { const struct xdiag_network *n = &s->network;
		emit(",\"value\":{\"link_up\":%s,\"tx\":%" PRIu64 ",\"rx\":%" PRIu64
		     ",\"tx_drop\":%" PRIu64 ",\"rx_drop\":%" PRIu64 ",\"pool_busy\":%" PRIu64
		     ",\"protocol_errors\":%" PRIu64 "}}", n->link_up ? "true" : "false",
		     n->tx, n->rx, n->tx_drop, n->rx_drop, n->pool_busy, n->protocol_errors); }
	emit(",\"timing\":{"); status(sh, s->timing_rc);
	if (s->timing_rc) { emit(",\"value\":null}"); }
	else { const struct xdiag_timing *t = &s->timing;
		emit(",\"value\":{\"period_us\":%" PRIu64 ",\"deadline_us\":%" PRIu64
		     ",\"loop_seq\":%" PRIu64 ",\"in_flight\":%s,\"deadline_miss_count\":%" PRIu64
		     ",\"skipped_release_count\":%" PRIu64 ",\"last_jitter_us\":", t->period_us,
		     t->deadline_us, t->loop_seq, t->in_flight ? "true" : "false",
		     t->deadline_miss_count, t->skipped_release_count);
		if (!t->interval.count) { emit("null"); } else { emit("%" PRId64, t->last_jitter_us); }
		emit(",\"last_completed_age_us\":");
		uint64_t now;
		if (!t->loop_seq || xdiag_clock_us(&now) || now < t->last_completion_us) { emit("null"); }
		else { emit("%" PRIu64, now - t->last_completion_us); }
		metric(sh, "interval", &t->interval); metric(sh, "absolute_jitter", &t->absolute_jitter);
		metric(sh, "schedule_lateness", &t->lateness); metric(sh, "step_elapsed", &t->elapsed);
		emit(",\"counters_saturated\":%s", t->saturated ? "true" : "false");
		emit("}}"); }
	shell_print(sh, "}");
	k_mutex_unlock(&output_lock);
	return 0;
}
