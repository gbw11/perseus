/* SPDX-License-Identifier: Apache-2.0 */
/* One app thread owns v1, Publisher's worker owns v2. Measurement output is
 * local and on-demand AFTER a bounded window; nothing prints on the hot path. */
#include <xenova/drive_runtime.h>
#include <xenova/telemetry.h>
#include <zephyr/shell/shell.h>
#include <zephyr/version.h>
#include <zephyr/sys/atomic.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if defined(CONFIG_XENOVA_RUNTIME)
#include <xenova/runtime.h>
#include <xenova/shutdown.h>
#include <xenova/netfront.h>
#include <zephyr/net/net_if.h>
#endif

static struct xdiag_loop loop;
static struct xt_bootstrap app_bootstrap;
static struct xt_inventory inventory;
static atomic_t stopping, app_quiesced;
static atomic_t clock_rc, task_rc;
static atomic_t app_close_error;
K_SEM_DEFINE(app_done, 0, 1);
static const char *app;
static bool client_ready;
static bool app_running, start_attempted;
K_MUTEX_DEFINE(xd_lifecycle_lock);
static struct k_spinlock admission_lock;
static bool admit(void)
{
	k_spinlock_key_t key = k_spin_lock(&admission_lock);
	bool allowed = !atomic_get(&stopping);
	k_spin_unlock(&admission_lock, key);
	return allowed;
}
static void stop_request(void)
{
	k_spinlock_key_t key = k_spin_lock(&admission_lock);
	atomic_set(&stopping, 1);
	k_spin_unlock(&admission_lock, key);
}
static struct xm_zclient *app_client;
static int64_t retry_at;
static uint64_t app_start_ms;
#if defined(CONFIG_XENOVA_RUNTIME)
static int64_t cleanup_deadline;
static int network_rc = -EAGAIN;
#endif

/* Values are guest monotonic microseconds. IDs belong to the v1 app session,
 * not the v2 Publisher. A zero message counter means no envelope was built. */
struct row {
	uint64_t begin, step_end, submit_begin, submit_end, iteration_end, release, iteration_begin;
	uint64_t completed, missed, skipped;
	int64_t input_seq, control_seq, input_age_ms;
	uint32_t generation, message, topic_seq, v_bits, steer_bits;
	int rc, encoded;
	bool attempted;
};
static struct row current, rows[CONFIG_XENOVA_DRIVE_CAPTURE_ROWS];
static uint64_t generated, attempted, accepted, failed, dropped;
static uint64_t capture_start, capture_end, overflow;
static size_t count;
static bool capture_complete, counter_saturated;
static struct k_spinlock capture_lock;
K_MUTEX_DEFINE(xd_output_lock);

static uint64_t now_us(void)
{
	uint64_t now;
	/* A failed clock is an explicit fault: never fabricate timestamp zero. */
	int rc = xdiag_clock_us(&now);
	if (rc) { atomic_set(&clock_rc, rc); atomic_set(&stopping, 1); return UINT64_MAX; }
	return now;
}

static int bootstrap_load(struct xt_bootstrap *out, int64_t end, void *channel)
{
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
	if (k_uptime_get() >= end) { return -ETIMEDOUT; }
	const char *path = getenv(channel ? "XD_TEST_APP_BOOTSTRAP" : "XT_TEST_BOOTSTRAP");
	const char *own = getenv("XT_TEST_INSTANCE_ID");
	if (!path || !own) { return -ENOENT; }
	FILE *f = fopen(path, "rb");
	if (!f) { return -errno; }
	char json[2049] = {0};
	size_t bytes = fread(json, 1, sizeof(json) - 1, f);
	bool extra = fgetc(f) != EOF; fclose(f);
	int rc = extra ? -EOVERFLOW : xt_bootstrap_decode(json, bytes, own, out);
	for (volatile char *p = json; p < json + sizeof(json); ++p) { *p = 0; }
	return rc;
#elif defined(CONFIG_XENOVA_RUNTIME)
	return xt_xen_bootstrap_load(out, end, channel);
#else
	ARG_UNUSED(out); ARG_UNUSED(end); ARG_UNUSED(channel); return -ENOTSUP;
#endif
}

#if defined(CONFIG_XENOVA_RUNTIME)
static int network(struct xdiag_network *out, void *context)
{
	ARG_UNUSED(context);
	if (network_rc) { return network_rc; }
	struct xnf_stats s;
	int rc = xnet_stats(&s); if (rc) { return rc; }
	struct net_if *iface = net_if_get_default();
	if (!iface) { return -ENODEV; }
	*out = (struct xdiag_network){.link_up = net_if_is_up(iface) && net_if_is_carrier_ok(iface),
		.tx = s.tx, .rx = s.rx, .tx_drop = s.tx_drop, .rx_drop = s.rx_drop,
		.pool_busy = s.pool_busy, .protocol_errors = s.protocol_errors};
	return 0;
}

static int quiesce(const char *request, int64_t end, void *context)
{
	ARG_UNUSED(request); ARG_UNUSED(context);
	stop_request();
#if defined(CONFIG_XENOVA_NETFRONT)
	xnet_stop_request();
#endif
	int64_t left = end - k_uptime_get();
	if (left <= 0 || k_mutex_lock(&xd_lifecycle_lock, K_MSEC(left))) { return -ETIMEDOUT; }
	bool wait_app = app_running;
	k_mutex_unlock(&xd_lifecycle_lock);
	/* Initialization failure never admitted the app loop. Only a running app's
	 * owner may certify that v1 use has ended; do not synthesize app_done. */
	left = end - k_uptime_get();
	if (wait_app && !atomic_get(&app_quiesced) &&
	    (left <= 0 || k_sem_take(&app_done, K_MSEC(left)))) { return -ETIMEDOUT; }
	if (atomic_get(&app_close_error)) { return atomic_get(&app_close_error); }
	/* App owner has aborted v1 before Publisher stops. Runtime closes netfront. */
	return xt_stop(end);
}
#endif

int xd_start(const char *app_id, uint32_t period_ms)
{
	if (k_mutex_lock(&xd_lifecycle_lock, K_MSEC(1000))) { return -ETIMEDOUT; }
	if (start_attempted || atomic_get(&stopping)) { k_mutex_unlock(&xd_lifecycle_lock); return -EALREADY; }
	start_attempted = true;
	int result = 0;
	app = app_id; app_start_ms = k_uptime_get();
	(void)xdiag_boot("drive-main", 0);
#if defined(CONFIG_XENOVA_RUNTIME)
	int rc = xshutdown_start(quiesce, NULL);
	(void)xdiag_boot("shutdown-handler", rc); if (rc) { result = rc; goto finished; }
	if (atomic_get(&stopping)) { result = -ECANCELED; goto finished; }
	network_rc = xnet_start();
	(void)xdiag_network_register(network, NULL);
	(void)xdiag_boot("ethernet-start", network_rc);
	if (network_rc) { result = network_rc; goto finished; }
#endif
	if (atomic_get(&stopping)) { result = -ECANCELED; goto finished; }
	inventory = (struct xt_inventory){.os_version = KERNEL_VERSION_STRING,
		.build_id = "drive-diagnostics-telemetry-2.0", .profile = "driving_app",
		.task_id = app, .interface_name = "netfront", .diagnostics = true,
		.publisher = true, .timing = true,
		.threads = IS_ENABLED(CONFIG_XENOVA_DIAGNOSTICS_THREADS),
		.heap = IS_ENABLED(CONFIG_XENOVA_DIAGNOSTICS_HEAP)};
	result = xdiag_loop_init(&loop, period_ms * 1000U, period_ms * 1000U);
	if (!result) { result = xdiag_loop_watch(&loop); }
	if (!result && admit()) { result = xt_start(bootstrap_load, NULL, &inventory); }
	if (!result && atomic_get(&stopping)) { result = -ECANCELED; }
	(void)xdiag_boot("drive-ready", result);
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
	k_spinlock_key_t key = k_spin_lock(&capture_lock);
	capture_start = now_us(); capture_end = UINT64_MAX;
	k_spin_unlock(&capture_lock, key);
#endif
finished:
	app_running = !result;
	if (result) { atomic_set(&stopping, 1); (void)xdiag_error("drive-init", result); }
	k_mutex_unlock(&xd_lifecycle_lock);
	return result;
}

bool xd_stopping(void)
{
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
	static bool requested;
	if (!requested && k_uptime_get() - app_start_ms >= 4000) {
		requested = xt_snapshot_request(true) == 0;
	}
	const char *duration = getenv("XD_TEST_DURATION_MS");
	if (duration && k_uptime_get() - app_start_ms >= strtoll(duration, NULL, 10)) {
		atomic_set(&stopping, 1);
	}
#endif
	return atomic_get(&stopping) != 0;
}

int xd_client_prepare(struct xm_zclient *client, const char *control, const char *input)
{
	if (!admit()) { return -ESHUTDOWN; }
	if (client_ready) { return 0; }
	if (k_uptime_get() < retry_at) { return -EAGAIN; }
	retry_at = k_uptime_get() + 1000;
	int rc = bootstrap_load(&app_bootstrap, k_uptime_get() + 500, "xenova-drive");
	if (rc) { return rc; }
	if (!admit()) { return -ESHUTDOWN; }
	struct xm_zconfig config = {.host = app_bootstrap.broker_ipv4, .port = app_bootstrap.port,
		.username = app_bootstrap.username, .password = app_bootstrap.password, .keepalive_s = 30,
		.boot_id = app_bootstrap.identity.boot_id, .instance_id = app_bootstrap.identity.publisher_id};
	rc = xm_zclient_init(client, app_bootstrap.identity.vm_id, NULL, app, &config);
	if (!rc) { rc = xm_zclient_subscribe(client, control, 1); }
	if (!rc) { rc = xm_zclient_subscribe(client, input, 0); }
	if (!rc) {
		client_ready = true;
		k_spinlock_key_t key = k_spin_lock(&capture_lock);
		app_client = client;
		k_spin_unlock(&capture_lock, key);
		printk("XD_READY {\"vm_id\":\"%s\",\"app_id\":\"%s\",\"boot_id\":\"%s\",\"agent_instance_id\":\"%s\",\"execution_id\":\"%s\"}\n",
			app_bootstrap.identity.vm_id, app, client->id.boot_id, client->id.instance_id,
			app_bootstrap.identity.execution_id);
	}
	return rc;
}
int xd_publish(struct xm_zclient *client,const char *topic,const char *type,const char *payload)
{
	/* A reservation before stop may finish; quiesce waits the app owner before
	 * touching v1 resources. No new publish/poll reservation follows stop. */
	return admit() ? xm_zclient_publish(client,topic,type,payload) : -ESHUTDOWN;
}
int xd_poll(struct xm_zclient *client,int32_t timeout_ms)
{
	return admit() ? xm_zclient_poll(client,timeout_ms) : -ESHUTDOWN;
}

int xd_iteration_begin(void)
{
	memset(&current, 0, sizeof(current));
	current.iteration_begin = now_us();
	return current.iteration_begin == UINT64_MAX ? -EIO : 0;
}

int xd_step_begin(void)
{
	int rc = xdiag_loop_begin(&loop);
	if (!rc) {
		current.begin = loop.timing.start_us; current.release = loop.timing.release_us;
		current.missed = loop.timing.deadline_miss_count;
		current.skipped = loop.timing.skipped_release_count;
	}
	else { atomic_set(&task_rc, rc); atomic_set(&stopping, 1); (void)xdiag_error("drive-step-begin", rc); }
	return rc;
}

int xd_step_end(void)
{
	int rc = xdiag_loop_end(&loop);
	if (!rc) {
		current.step_end = loop.timing.last_completion_us;
		current.completed = loop.timing.loop_seq;
		/* Per-completion increments remain valid for an arbitrary capture window. */
		current.missed = loop.timing.deadline_miss_count - current.missed;
		current.skipped = loop.timing.skipped_release_count - current.skipped;
	}
	else { atomic_set(&task_rc, rc); atomic_set(&stopping, 1); (void)xdiag_error("drive-step-end", rc); }
	return rc;
}

void xd_command(struct xm_zclient *client, const char *topic, const char *json, int encoded,
		uint32_t generation, float v_mps, float steer_rad, int64_t input_seq,
		int64_t control_seq, int64_t input_age_ms)
{
	current.generation = generation; current.input_seq = input_seq; current.control_seq = control_seq;
	current.input_age_ms = input_age_ms;
	current.encoded = encoded;
	BUILD_ASSERT(sizeof(float) == sizeof(uint32_t));
	memcpy(&current.v_bits, &v_mps, sizeof(v_mps));
	memcpy(&current.steer_bits, &steer_rad, sizeof(steer_rad));
	current.submit_begin = now_us();
	current.rc = encoded < 0 ? encoded : -ESHUTDOWN;
	uint32_t before = client->message_counter;
	if (encoded >= 0 && !xd_stopping() && client_ready) {
		current.attempted = true;
		current.rc = xd_publish(client, topic, "drive.command", json);
	} else if (encoded >= 0 && !xd_stopping()) {
		current.rc = -ENOTCONN;
	}
	if (client->message_counter != before) { current.message = client->message_counter; }
	if (!current.rc) {
		for (size_t i = 0; i < ARRAY_SIZE(client->pubs); ++i) {
			if (!strcmp(client->pubs[i].topic, topic)) { current.topic_seq = client->pubs[i].seq; break; }
		}
	}
	current.submit_end = now_us();
}

static void increment(uint64_t *value)
{
	if (*value == UINT64_MAX) { counter_saturated = true; }
	else { ++*value; }
}

void xd_iteration_end(void)
{
	current.iteration_end = now_us();
	k_spinlock_key_t key = k_spin_lock(&capture_lock);
	increment(&generated);
	if (current.attempted) {
		increment(&attempted);
		increment(current.rc ? &failed : &accepted);
	} else { increment(&dropped); }
	if (capture_end && current.begin >= capture_start && current.begin < capture_end) {
		if (count < ARRAY_SIZE(rows)) { rows[count++] = current; } else { increment(&overflow); }
	}
	if (capture_end && current.iteration_end >= capture_end) { capture_complete = true; }
	k_spin_unlock(&capture_lock, key);
}

uint64_t xd_next_release_us(void) { return loop.timing.next_release_us; }

void xd_latest(struct xm_zclient *client, const char *topic, const char *type,
		struct xm_span *payload, int64_t *age_ms, int64_t *seq)
{
	*payload = (struct xm_span){0}; *age_ms = 0; *seq = -1;
	struct xm_envelope env;
	if (client_ready && !xm_zclient_latest(client, topic, &env, age_ms) && xm_span_eq(env.type, type)) {
		*payload = env.payload; *seq = env.seq;
	}
}

#define emit(sh, ...) do { if (sh) { shell_print(sh, __VA_ARGS__); } else { printf(__VA_ARGS__); printf("\n"); } } while (0)

static void output_row(const struct shell *sh, const struct row *r)
{
	emit(sh, "XD_ROW [\"%" PRIu64 "\",\"%" PRIu64 "\",\"%" PRIu64 "\","
		"\"%" PRIu64 "\",\"%" PRIu64 "\",\"%" PRIu64 "\","
		"\"%" PRIu64 "\",\"%" PRIu64 "\",\"%" PRIu64 "\","
		"\"%" PRId64 "\",\"%" PRId64 "\",%u,%u,%u,%d,%d,%u,%u,"
		"\"%" PRId64 "\",\"%" PRIu64 "\",%u]",
		r->begin, r->step_end, r->submit_begin, r->submit_end, r->iteration_end, r->release,
		r->completed, r->missed, r->skipped, r->input_seq, r->control_seq,
		r->generation, r->message, r->topic_seq, r->rc, r->encoded, r->v_bits, r->steer_bits, r->input_age_ms, r->iteration_begin,
		r->attempted ? 1U : 0U);
}

static int drive_cmd_inner(const struct shell *sh, size_t argc, char **argv)
{
	bool records = argc >= 2 && !strcmp(argv[1], "records");
	if (argc == 4 && !strcmp(argv[1], "capture")) {
		char *tail;
		unsigned long delay = strtoul(argv[2], &tail, 10); if (!*argv[2] || *tail || delay > 60000) { return -EINVAL; }
		unsigned long duration = strtoul(argv[3], &tail, 10); if (!*argv[3] || *tail || !duration || duration > 240000) { return -EINVAL; }
		uint64_t now = now_us(); if (now == UINT64_MAX) { return -EIO; }
		k_spinlock_key_t key = k_spin_lock(&capture_lock);
		if (capture_end && !capture_complete) { k_spin_unlock(&capture_lock, key); return -EBUSY; }
		capture_start = now + delay * 1000U; capture_end = capture_start + duration * 1000U;
		count = 0; overflow = 0;
		capture_complete = false;
		k_spin_unlock(&capture_lock, key);
	} else if (argc != 1 && !(records && argc <= 4)) { return -EINVAL; }
	/* Snapshot metadata under the same lock as capture. No long dump holds it. */
	k_spinlock_key_t key = k_spin_lock(&capture_lock);
	size_t size = count; uint64_t lost = overflow, first = capture_start, end = capture_end;
	bool complete = capture_complete, saturated = counter_saturated;
	uint64_t gen = generated, attempts = attempted, accepts = accepted, failures = failed, drops = dropped;
	struct xm_zclient *client = app_client;
	k_spin_unlock(&capture_lock, key);
	if (!client) { return -ENODATA; }
	if (records && !complete && !atomic_get(&app_quiesced)) { return -EBUSY; }
	/* Xen PV console has a bounded output ring. A consumer acknowledges each
	 * small page before requesting the next; a bulk shell dump can lose rows. */
	size_t offset = 0, limit = sh ? 4 : size;
	if (records && argc >= 3) {
		char *tail;
		unsigned long value = strtoul(argv[2], &tail, 10);
		if (!*argv[2] || *tail || value > size) { return -EINVAL; }
		offset = value;
	}
	if (records && argc == 4) {
		char *tail;
		unsigned long value = strtoul(argv[3], &tail, 10);
		if (!*argv[3] || *tail || !value || value > 4) { return -EINVAL; }
		limit = value;
	}
	if (!records || !offset) {
		emit(sh, "XD_SUMMARY {\"schema_version\":1,\"app\":\"%s\",\"generated\":\"%" PRIu64 "\",\"attempted\":\"%" PRIu64 "\",\"accepted\":\"%" PRIu64 "\",\"failed\":\"%" PRIu64 "\",\"dropped\":\"%" PRIu64 "\",\"capture_start_us\":\"%" PRIu64 "\",\"capture_end_us\":\"%" PRIu64 "\",\"rows\":%zu,\"overflow\":\"%" PRIu64 "\",\"capture_complete\":%s,\"counter_saturated\":%s,\"clock_rc\":%d,\"task_rc\":%d}",
			app, gen, attempts, accepts, failures, drops, first, end, size, lost,
			complete ? "true" : "false", saturated ? "true" : "false", (int)atomic_get(&clock_rc), (int)atomic_get(&task_rc));
	}
	if (records) {
		if (!offset) {
			emit(sh, "XD_SESSION {\"vm_id\":\"%s\",\"app_id\":\"%s\",\"boot_id\":\"%s\",\"agent_instance_id\":\"%s\",\"execution_id\":\"%s\"}",
				app_bootstrap.identity.vm_id, app, client->id.boot_id, client->id.instance_id,
				app_bootstrap.identity.execution_id);
		}
		size_t next = MIN(size, offset + limit);
		for (size_t i = offset; i < next; ++i) { output_row(sh, &rows[i]); }
		if (sh) { emit(sh, "XD_PAGE {\"offset\":%zu,\"next\":%zu,\"total\":%zu}", offset, next, size); }
	}
	return 0;
}

static int drive_cmd(const struct shell *sh, size_t argc, char **argv)
{
	if (k_mutex_lock(&xd_output_lock, K_NO_WAIT)) { return -EBUSY; }
	int rc = drive_cmd_inner(sh, argc, argv);
	k_mutex_unlock(&xd_output_lock);
	return rc;
}

int xd_finish(struct xm_zclient *client, int app_result)
{
	stop_request();
	if (!app_result) { app_result = atomic_get(&task_rc); }
	if (!app_result) { app_result = atomic_get(&clock_rc); }
	if (app_result) { atomic_set(&task_rc, app_result); (void)xdiag_error("drive-exit", app_result); }
	/* Runs only in the app owner, after its final I/O returned. */
	if (client_ready) { xm_zclient_close(client); client_ready = false; }
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
	if (client && client->socket_io.close_error) { atomic_set(&app_close_error, client->socket_io.close_error); }
#endif
	if (app_running) {
		atomic_set(&app_quiesced, 1);
		k_sem_give(&app_done);
	}
	int cleanup = atomic_get(&app_close_error);
#if defined(CONFIG_XENOVA_RUNTIME)
	if (!cleanup_deadline) { cleanup_deadline = k_uptime_get() + CONFIG_XENOVA_SHUTDOWN_BUDGET_MS; }
	int64_t end = cleanup_deadline;
	int monitor = xshutdown_stop(end);
	if (monitor == -EBUSY) {
		/* A valid external request owns the shared teardown. app_done above
		 * releases its quiesce; joining that handler here would deadlock. */
		return app_result ? app_result : atomic_get(&app_close_error);
	}
	if (monitor && !cleanup) { cleanup = monitor; }
	int rc = xt_stop(end); if (rc && !cleanup) { cleanup = rc; }
	if (!rc && !atomic_get(&app_close_error)) {
		rc = xnet_close(end); if (rc && !cleanup) { cleanup = rc; }
		if (!rc && !cleanup) { cleanup = xsr_close(end); }
	}
	(void)xdiag_boot("drive-cleanup", cleanup);
#endif
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
	k_spinlock_key_t key = k_spin_lock(&capture_lock);
	capture_end = now_us(); capture_complete = true;
	k_spin_unlock(&capture_lock, key);
	uint64_t before = now_us();
	int rc = xt_stop(k_uptime_get() + 1000);
	struct xt_status state;
	int status_rc = xt_status_get(&state);
	printf("XD_STOP {\"rc\":%d,\"status_rc\":%d,\"elapsed_us\":\"%" PRIu64 "\",\"worker_running\":%s}\n",
		rc, status_rc, now_us() - before, !status_rc && state.running ? "true" : "false");
	printf("XD_CONNECTION {\"attempts\":%u,\"last_io_rc\":%d,\"received\":%u}\n",
		client->connection_attempts, client->last_io_rc, client->received);
	/* Native test output is after stopping, never a 50Hz console producer. */
	char *argv[] = {"drive", "records"};
	(void)drive_cmd(NULL, 2, argv);
	fflush(stdout);
	exit(app_result || cleanup || rc || status_rc || state.running ? 1 : 0);
#endif
	return app_result ? app_result : cleanup;
}
void xd_client_stopped(struct xm_zclient *client) { (void)xd_finish(client, 0); }

SHELL_STATIC_SUBCMD_SET_CREATE(xenova_commands,
	SHELL_CMD(diag, NULL, "Local Core snapshot; threads is intrusive", xdiag_shell_cmd),
	SHELL_CMD(telemetry, NULL, "Separate v2 Publisher status/snapshot", xt_shell_cmd),
	SHELL_CMD(drive, NULL, "Source observer: capture <delay_ms> <duration_ms>; records [offset] [1..4] after window", drive_cmd),
	SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(xenova, &xenova_commands, "Xenova common diagnostics", NULL);
