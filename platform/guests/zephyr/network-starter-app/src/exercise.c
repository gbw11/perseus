/* SPDX-License-Identifier: Apache-2.0 */
/* Optional bounded arithmetic job. No drive.command, MQTT or vehicle output. */
#include <zephyr/kernel.h>
#include <stdatomic.h>
#include <zephyr/shell/shell.h>
#include <inttypes.h>
#if defined(CONFIG_XENOVA_DIAGNOSTICS)
#include <xenova/diagnostics.h>
static struct xdiag_loop measured;
#endif
#include "diagnostics_app.h"

K_THREAD_STACK_DEFINE(exercise_stack, 2048);
static struct k_thread exercise_thread;
static atomic_bool stopping, done;
static volatile uint32_t checksum;
static bool started;
/* Same outside-hook observer in both variants, solely for on/off comparison.
 * Aggregate only; its own cost remains present in both measured binaries. */
static struct k_spinlock observer_lock;
static struct {
	uint64_t completed, intervals, last_start, max_interval_us;
	uint64_t max_absolute_jitter_us, max_lateness_us, max_job_us;
} observer;

static void observe(uint64_t start, uint64_t finish, uint64_t release)
{
	k_spinlock_key_t key = k_spin_lock(&observer_lock);
	if (observer.completed) {
		uint64_t interval = start - observer.last_start;
		uint64_t jitter = interval > 20000 ? interval - 20000 : 20000 - interval;
		observer.max_interval_us = MAX(observer.max_interval_us, interval);
		observer.max_absolute_jitter_us = MAX(observer.max_absolute_jitter_us, jitter);
		++observer.intervals;
	}
	observer.last_start = start;
	observer.max_lateness_us = MAX(observer.max_lateness_us, start - release);
	observer.max_job_us = MAX(observer.max_job_us, finish - start);
	++observer.completed;
	k_spin_unlock(&observer_lock, key);
}

static void exercise(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	const uint64_t period = 20000;
	uint64_t next = k_cyc_to_us_floor64(k_cycle_get_64());
#if defined(CONFIG_XENOVA_DIAGNOSTICS)
	int rc = xdiag_loop_init(&measured, period, period);
	if (!rc) { rc = xdiag_loop_watch(&measured); }
	if (rc) { (void)xdiag_error("exercise-init", rc); atomic_store(&done, true); return; }
	next = measured.timing.next_release_us;
#endif
	while (!atomic_load(&stopping)) {
		uint64_t now = k_cyc_to_us_floor64(k_cycle_get_64());
		if (now < next) { k_usleep(next - now); continue; }
#if defined(CONFIG_XENOVA_DIAGNOSTICS)
		rc = xdiag_loop_begin(&measured);
		if (rc) { (void)xdiag_error("exercise-begin", rc); break; }
#endif
		uint32_t value = checksum;
		for (unsigned i = 0; i < 128; ++i) { value = value * 1664525U + 1013904223U; }
		checksum = value;
#if defined(CONFIG_XENOVA_DIAGNOSTICS)
		rc = xdiag_loop_end(&measured);
		if (rc) { (void)xdiag_error("exercise-end", rc); break; }
#endif
		uint64_t end = k_cyc_to_us_floor64(k_cycle_get_64());
		observe(now, end, next);
		/* Identical scheduling/work in both builds, independent of measurement. */
		next += period;
		if (end > next) { next += ((end - next - 1) / period + 1) * period; }
	}
	atomic_store(&done, true);
}

void starter_exercise_start(void)
{
	started = true;
	k_thread_create(&exercise_thread, exercise_stack, K_THREAD_STACK_SIZEOF(exercise_stack),
			exercise, NULL, NULL, NULL, 8, 0, K_NO_WAIT);
	(void)k_thread_name_set(&exercise_thread, "diag-exercise");
}

int starter_exercise_stop(int64_t deadline)
{
	if (!started) { return 0; }
	atomic_store(&stopping, true);
	while (!atomic_load(&done)) {
		if (k_uptime_get() >= deadline) { return -ETIMEDOUT; }
		k_sleep(K_MSEC(1));
	}
	return 0;
}

int starter_exercise_cmd(const struct shell *sh, size_t argc, char **argv)
{
	ARG_UNUSED(argv);
	if (argc != 1) { return -EINVAL; }
	k_spinlock_key_t key = k_spin_lock(&observer_lock);
	uint64_t completed = observer.completed, intervals = observer.intervals;
	uint64_t interval = observer.max_interval_us, jitter = observer.max_absolute_jitter_us;
	uint64_t lateness = observer.max_lateness_us, job = observer.max_job_us;
	k_spin_unlock(&observer_lock, key);
	shell_fprintf(sh, SHELL_NORMAL,
		"{\"diagnostics_enabled\":%s,\"period_us\":20000,\"completed\":%" PRIu64
		",\"interval_samples\":%" PRIu64 ",\"value\":", IS_ENABLED(CONFIG_XENOVA_DIAGNOSTICS) ?
		"true" : "false", completed, intervals);
	if (!completed) { shell_print(sh, "null}"); return 0; }
	shell_fprintf(sh, SHELL_NORMAL, "{\"max_job_with_hooks_us\":%" PRIu64
		",\"max_lateness_us\":%" PRIu64 ",\"max_interval_us\":", job, lateness);
	if (!intervals) { shell_fprintf(sh, SHELL_NORMAL, "null,\"max_absolute_jitter_us\":null"); }
	else { shell_fprintf(sh, SHELL_NORMAL, "%" PRIu64 ",\"max_absolute_jitter_us\":%" PRIu64,
		interval, jitter); }
	shell_print(sh, "}}");
	return 0;
}
