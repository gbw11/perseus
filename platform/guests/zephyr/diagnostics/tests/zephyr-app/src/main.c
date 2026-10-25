/* SPDX-License-Identifier: Apache-2.0 */
/* Real Zephyr adapter tests; network callback is an explicit fault fixture. */
#include <xenova/diagnostics.h>
#include <zephyr/ztest.h>
#include <zephyr/shell/shell.h>
#include <zephyr/shell/shell_dummy.h>
#include <errno.h>
#include <string.h>

static struct xdiag_snapshot snapshot;
static struct xdiag_loop loop;
static int provider_rc;
static int provider(struct xdiag_network *out, void *context)
{
	ARG_UNUSED(context);
	out->tx = 42;
	out->link_up = true;
	return provider_rc;
}
SHELL_CMD_REGISTER(diag, NULL, "Local diagnostics test", xdiag_shell_cmd);

ZTEST(diagnostics, test_actual_threads_heap_and_errors)
{
	zassert_ok(xdiag_boot("test-stage", 0));
	zassert_ok(xdiag_error("test-failure", -EIO));
	zassert_ok(xdiag_error("진단", -EIO));
	zassert_ok(xdiag_error("quote\"\\\n", -EIO));
	zassert_ok(xdiag_snapshot_get(&snapshot, true));
	zassert_true(snapshot.history.error_count >= 1);
	zassert_equal(snapshot.history.errors[snapshot.history.error_entries - 1].code, -EIO);
#if defined(CONFIG_XENOVA_DIAGNOSTICS_THREADS)
	if (snapshot.threads_seen > ARRAY_SIZE(snapshot.threads)) {
		zassert_equal(snapshot.threads_rc, -ENOSPC);
	} else { zassert_ok(snapshot.threads_rc); }
	zassert_true(snapshot.thread_count > 0);
	bool found = false;
	for (size_t i = 0; i < snapshot.thread_count; ++i) {
		struct xdiag_thread *t = &snapshot.threads[i];
		if (!t->stack_rc) {
			zassert_true(t->stack_bytes > 0);
			zassert_true(t->unused_bytes <= t->stack_bytes);
			found = true;
		} else {
			zassert_equal(t->stack_rc, -E2BIG);
		}
	}
	if (CONFIG_XENOVA_DIAGNOSTICS_STACK_SCAN_LIMIT >= CONFIG_TEST_EXTRA_STACK_SIZE + 1024) {
		zassert_true(found);
	}
#else
	zassert_equal(snapshot.threads_rc, -ENOTSUP);
	zassert_equal(snapshot.thread_count, 0);
#endif
#if defined(CONFIG_XENOVA_DIAGNOSTICS_HEAP)
	zassert_ok(snapshot.heap_rc);
	size_t before = snapshot.heap_allocated_bytes;
	void *allocation = k_malloc(256);
	zassert_not_null(allocation);
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_true(snapshot.heap_allocated_bytes >= before + 256);
	zassert_true(snapshot.heap_peak_bytes >= snapshot.heap_allocated_bytes);
	k_free(allocation);
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_equal(snapshot.heap_allocated_bytes, before);
#else
	zassert_equal(snapshot.heap_rc, -ENOTSUP);
#endif
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_equal(snapshot.threads_rc, -EAGAIN);
}

ZTEST(diagnostics, test_clock_and_completed_progress)
{
#if !defined(CONFIG_XENOVA_DIAGNOSTICS_TIMING)
	uint64_t out = 123;
	zassert_equal(xdiag_clock_us(&out), -ENOTSUP);
	zassert_equal(out, 123);
	zassert_equal(xdiag_loop_init(&loop, 20000, 20000), -ENOTSUP);
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_equal(snapshot.clock_rc, -ENOTSUP);
	zassert_equal(snapshot.timing_rc, -ENOENT);
#else
	uint64_t start, end;
	zassert_ok(xdiag_clock_us(&start));
	int64_t uptime = k_uptime_get();
	k_sleep(K_MSEC(20));
	zassert_ok(xdiag_clock_us(&end));
	zassert_true(end > start);
	int64_t discrepancy = (int64_t)(end - start) - (k_uptime_get() - uptime) * 1000;
	zassert_true(discrepancy > -2000 && discrepancy < 2000);
	zassert_ok(xdiag_loop_init(&loop, 20000, 20000));
	zassert_ok(xdiag_loop_watch(&loop));
	zassert_ok(xdiag_loop_begin(&loop));
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_true(snapshot.timing.in_flight);
	zassert_equal(snapshot.timing.loop_seq, 0);
	k_sleep(K_MSEC(2));
	zassert_ok(xdiag_loop_end(&loop));
	zassert_ok(xdiag_snapshot_get(&snapshot, false));
	zassert_equal(snapshot.timing.loop_seq, 1);
	zassert_false(snapshot.timing.in_flight);
	zassert_true(snapshot.timing.elapsed.last >= 2000);
	zassert_ok(snapshot.clock_rc);
	zassert_true(snapshot.cycle_hz > 0);
#endif
}

ZTEST(diagnostics, test_network_failure_and_shell_json)
{
	zassert_ok(xdiag_network_register(provider, NULL));
	zassert_equal(xdiag_network_register(provider, NULL), -EALREADY);
	const struct shell *sh = shell_backend_dummy_get_ptr();
	for (unsigned i = 0; !shell_ready(sh) && i < 100; ++i) { k_sleep(K_MSEC(1)); }
	zassert_true(shell_ready(sh));
	shell_backend_dummy_clear_output(sh);
	provider_rc = -ETIMEDOUT;
	zassert_ok(shell_execute_cmd(sh, "diag"));
	size_t size;
	const char *output = shell_backend_dummy_get_output(sh, &size);
	zassert_not_null(strstr(output, "\"network\":{\"status\":\"collection_failed\""));
	zassert_not_null(strstr(output, "\"value\":null"));
	printk("XDIAG_JSON_FAILURE %s\n", output);
	provider_rc = 0;
	shell_backend_dummy_clear_output(sh);
	zassert_ok(shell_execute_cmd(sh, "diag threads"));
	output = shell_backend_dummy_get_output(sh, &size);
	zassert_not_null(strstr(output, "\"tx\":42"));
	zassert_not_null(strstr(output, "\"status\":\"ok\""));
	printk("XDIAG_JSON_SUCCESS %s\n", output);
	zassert_equal(shell_execute_cmd(sh, "diag unsupported"), -EINVAL);
}
ZTEST_SUITE(diagnostics, NULL, NULL, NULL, NULL, NULL);
