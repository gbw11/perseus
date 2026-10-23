/* SPDX-License-Identifier: Apache-2.0 */
/* Small hot-path accumulators; Zephyr providers and output live elsewhere. */
#include <xenova/diagnostics_core.h>
#include <errno.h>
#include <limits.h>
#include <string.h>

static uint64_t add(uint64_t a, uint64_t b, bool *saturated)
{
	if (UINT64_MAX - a < b) {
		*saturated = true;
		return UINT64_MAX;
	}
	return a + b;
}

static void record(struct xdiag_event *ring, size_t *count,
		   const struct xdiag_event *event)
{
	if (*count == XDIAG_RECORDS) {
		memmove(ring, ring + 1, (XDIAG_RECORDS - 1) * sizeof(*ring));
	} else {
		++*count;
	}
	ring[*count - 1] = *event;
}

int xdiag_history_record(struct xdiag_history *h, const char *stage, int code,
			 uint64_t uptime_ms, bool boot_event)
{
	if (!h || !stage || !*stage) {
		return -EINVAL;
	}
	size_t len = 0;
	while (len < XDIAG_STAGE_BYTES && stage[len]) { ++len; }
	if (len >= XDIAG_STAGE_BYTES) {
		return -ENAMETOOLONG;
	}
	struct xdiag_event event = {.uptime_ms = uptime_ms, .code = code};
	memcpy(event.stage, stage, len + 1);
	if (boot_event) {
		if (h->boot_events < UINT64_MAX) { ++h->boot_events; }
		record(h->boot, &h->boot_count, &event);
	}
	if (code) {
		if (h->error_count < UINT64_MAX) { ++h->error_count; }
		record(h->errors, &h->error_entries, &event);
	}
	return 0;
}

static void metric(struct xdiag_metric *m, uint64_t value)
{
	unsigned bucket = 0;
	for (uint64_t n = value; n && bucket < XDIAG_HIST_BUCKETS - 1; n >>= 1) {
		++bucket;
	}
	if (!m->count || value < m->min) { m->min = value; }
	if (!m->count || value > m->max) { m->max = value; }
	m->last = value;
	m->sum = add(m->sum, value, &m->saturated);
	m->count = add(m->count, 1, &m->saturated);
	m->histogram[bucket] = add(m->histogram[bucket], 1, &m->saturated);
}

int xdiag_timing_init(struct xdiag_timing *t, uint64_t period,
		      uint64_t deadline, uint64_t origin)
{
	if (!t || !period || !deadline || period > INT64_MAX ||
	    deadline > INT64_MAX || origin > UINT64_MAX - deadline ||
	    origin > UINT64_MAX - period) {
		return -EINVAL;
	}
	memset(t, 0, sizeof(*t));
	t->period_us = period;
	t->deadline_us = deadline;
	t->next_release_us = origin;
	t->initialized = true;
	return 0;
}

int xdiag_timing_begin(struct xdiag_timing *t, uint64_t now)
{
	if (!t || !t->initialized) { return -EINVAL; }
	if (t->in_flight) { return -EBUSY; }
	if (now < t->last_completion_us) { return -EINVAL; }
	if (now < t->next_release_us) { return -EAGAIN; }
	if (now - t->next_release_us > INT64_MAX ||
	    (t->has_previous_start && now - t->previous_start_us > INT64_MAX) ||
	    t->next_release_us > UINT64_MAX - t->deadline_us) {
		return -ERANGE;
	}
	t->release_us = t->next_release_us;
	t->start_us = now;
	t->in_flight = true;
	metric(&t->lateness, now - t->release_us);
	if (t->has_previous_start) {
		uint64_t interval = now - t->previous_start_us;
		t->last_jitter_us = (int64_t)interval - (int64_t)t->period_us;
		metric(&t->interval, interval);
		metric(&t->absolute_jitter, (uint64_t)(t->last_jitter_us < 0 ?
			-t->last_jitter_us : t->last_jitter_us));
	}
	t->has_previous_start = true;
	t->previous_start_us = now;
	return 0;
}

int xdiag_timing_end(struct xdiag_timing *t, uint64_t now)
{
	if (!t || !t->initialized || !t->in_flight || now < t->start_us) {
		return -EINVAL;
	}
	if (t->release_us > UINT64_MAX - t->period_us) { return -ERANGE; }
	uint64_t next = t->release_us + t->period_us;
	uint64_t skipped = now > next ? (now - next - 1) / t->period_us + 1 : 0;
	if (skipped > (UINT64_MAX - next) / t->period_us) { return -ERANGE; }
	t->next_release_us = next + skipped * t->period_us;
	t->skipped_release_count = add(t->skipped_release_count, skipped, &t->saturated);
	if (now > t->release_us + t->deadline_us) {
		t->deadline_miss_count = add(t->deadline_miss_count, 1, &t->saturated);
	}
	metric(&t->elapsed, now - t->start_us);
	t->loop_seq = add(t->loop_seq, 1, &t->saturated);
	t->last_completion_us = now;
	t->in_flight = false;
	return 0;
}

int xdiag_percentile_upper(const struct xdiag_metric *m, unsigned p, uint64_t *out)
{
	if (!m || !out || !p || p > 100) { return -EINVAL; }
	if (!m->count) { return -ENODATA; }
	if (m->saturated) { return -ERANGE; }
	uint64_t rank = (m->count / 100) * p + ((m->count % 100) * p + 99) / 100;
	uint64_t seen = 0;
	for (unsigned i = 0; i < XDIAG_HIST_BUCKETS; ++i) {
		seen += m->histogram[i];
		if (seen >= rank) {
			if (i == XDIAG_HIST_BUCKETS - 1) { return -ERANGE; }
			*out = i ? (UINT64_C(1) << i) - 1 : 0;
			return 0;
		}
	}
	return -EINVAL;
}
