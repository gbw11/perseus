/* SPDX-License-Identifier: Apache-2.0 */
/* Calculation owns payload parsing; v1/v2 have separate I/O owners. Output is
 * local and on-demand AFTER a bounded window; nothing prints on the hot path. */
#include <xenova/drive_runtime.h>
#include <xenova/telemetry.h>
#if defined(CONFIG_XENOVA_DRIVE_WARM)
#include <xenova/drive_context.h>
#endif
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
static int64_t cleanup_deadline;
K_MUTEX_DEFINE(xd_lifecycle_lock);
static struct k_spinlock admission_lock;
static bool admit(void)
{
	k_spinlock_key_t key = k_spin_lock(&admission_lock);
	bool allowed = !atomic_get(&stopping);
	k_spin_unlock(&admission_lock, key);
	return allowed;
}
static void stop_request_until(int64_t deadline)
{
	k_spinlock_key_t key = k_spin_lock(&admission_lock);
	if (deadline > 0 && (!cleanup_deadline || deadline < cleanup_deadline)) { cleanup_deadline = deadline; }
	atomic_set(&stopping, 1);
	k_spin_unlock(&admission_lock, key);
	xd_io_stop_request();
}
static void stop_request(void) { stop_request_until(0); }
static int64_t cleanup_end(void)
{
#if defined(CONFIG_XENOVA_RUNTIME)
	int64_t proposed = k_uptime_get() + CONFIG_XENOVA_SHUTDOWN_BUDGET_MS;
#else
	int64_t proposed = k_uptime_get() + 1000;
#endif
	k_spinlock_key_t key=k_spin_lock(&admission_lock);
	if (!cleanup_deadline) { cleanup_deadline=proposed; }
	int64_t end=cleanup_deadline;
	k_spin_unlock(&admission_lock,key);return end;
}
static struct xm_zclient *app_client;
static int64_t retry_at;
#if defined(CONFIG_XENOVA_DRIVE_WARM)
static atomic_t lifecycle_queued,lifecycle_rejected,lifecycle_first_rejection;
#endif
static uint64_t app_start_ms;
#if defined(CONFIG_XENOVA_RUNTIME)
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
static struct row current;
static int64_t work_finished_ms;
#if defined(CONFIG_XENOVA_DRIVE_RECORDER)
static struct row rows[CONFIG_XENOVA_DRIVE_CAPTURE_ROWS];
static struct xd_delivery deliveries[CONFIG_XENOVA_DRIVE_CAPTURE_ROWS];
#endif
static struct xd_inputs cycle_inputs;
static int cycle_inputs_status;
static const struct xd_received *command_input=&cycle_inputs.input;
static int64_t command_valid_until;
static struct xd_candidate candidate;
static size_t delivery_count;
static uint64_t delivery_overflow;
static uint64_t generated, attempted, accepted, failed, dropped;
static uint64_t capture_start, capture_end, overflow;
static size_t count;
static bool capture_complete, counter_saturated;
/* Calculation-window completion and terminal transmission accounting are
 * separate. Freeze pages only when every admitted window candidate has a
 * terminal result; an in-flight socket is never a completed transmission. */
static bool transmissions_complete;
static uint64_t capture_admitted, capture_terminal;
static struct k_spinlock capture_lock;
static struct xdh_health auto_health,manual_health;
static bool health_observed[2];
static uint64_t health_completed[2],health_step_end[2];
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
	int rc = extra ? -EOVERFLOW : xt_bootstrap_decode_channel(json, bytes, own,
		channel ? channel : "xenova-telemetry", out);
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
	int rc = xnet_stats_try(&s); if (rc) { return rc; }
	struct net_if *iface = net_if_get_default();
	if (!iface) { return -ENODEV; }
	*out = (struct xdiag_network){.link_up = net_if_is_up(iface) && net_if_is_carrier_ok(iface),
		.tx = s.tx, .rx = s.rx, .tx_drop = s.tx_drop, .rx_drop = s.rx_drop,
		.pool_busy = s.pool_busy, .protocol_errors = s.protocol_errors,
		.tx_bytes=s.tx_bytes,.rx_bytes=s.rx_bytes,.counter_epoch=s.counter_epoch,
		.bytes_rc=s.bytes_saturated?-ERANGE:0};
	return 0;
}

static int quiesce(const char *request, int64_t end, void *context)
{
	ARG_UNUSED(request); ARG_UNUSED(context);
	/* Publish the original deadline with the stop gate, before main can
	 * observe stop and begin joining its communication owner. */
	stop_request_until(end);
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
	int rc = xd_io_join(end);
	if (rc) { return rc; }
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
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST) && defined(CONFIG_XENOVA_DRIVE_RECORDER)
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
	/* Native fixture only: request the same cooperative cleanup before its
	 * outer test timeout. Never used by a target/operating VM. */
	const char *stop_file = getenv("XD_TEST_STOP_FILE");
	if (stop_file) {
		FILE *request = fopen(stop_file, "r");
		if (request) { fclose(request); stop_request(); }
	}
	if (duration && k_uptime_get() - app_start_ms >= strtoll(duration, NULL, 10)) {
		atomic_set(&stopping, 1);
	}
#endif
	return atomic_get(&stopping) != 0;
}

bool xd_owner_admit(void)
{
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
	/* The test's external shutdown deadline applies to I/O admission too;
	 * it is not a promise that previously queued TCP bytes were delivered. */
	const char *duration = getenv("XD_TEST_DURATION_MS");
	if (duration && k_uptime_get()-app_start_ms >= strtoll(duration,NULL,10)) { stop_request(); }
#endif
	return admit();
}

int xd_owner_prepare(struct xm_zclient *client, const char *control, const char *input)
{
	if (!admit()) { return -ESHUTDOWN; }
	if (client_ready) { return 0; }
	if (k_uptime_get() < retry_at) { return -EAGAIN; }
	retry_at = k_uptime_get() + 1000;
	int rc;
#if defined(CONFIG_XENOVA_DRIVE_WARM)
	rc=xd_context_start(bootstrap_load);
	if(!rc)rc=xd_context_get(&app_bootstrap);
#else
	rc = bootstrap_load(&app_bootstrap, k_uptime_get() + 500, "xenova-drive");
#endif
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
void xd_owner_context(struct xd_inputs *out)
{
#if defined(CONFIG_XENOVA_DRIVE_WARM)
 struct xt_bootstrap value;
 memset(out->app_instances,0,sizeof(out->app_instances));
 if(!xd_context_get(&value)){
  strcpy(out->app_instances[0],value.auto_instance_id);strcpy(out->app_instances[1],value.manual_instance_id);
 }
 memset(value.password,0,sizeof(value.password));memset(value.world_password,0,sizeof(value.world_password));
#else
 ARG_UNUSED(out);
#endif
}
int xd_client_prepare(struct xm_zclient *client,const char *control,const char *input)
{
 return admit() ? xd_io_start(client,control,input) : -ESHUTDOWN;
}
bool xd_owner_instance(int index,const char *instance)
{
#if defined(CONFIG_XENOVA_DRIVE_WARM)
 struct xt_bootstrap value;
 if(index<0||index>1||!instance||!instance[0]||xd_context_get(&value))return false;
 bool matches=!strcmp(instance,index?value.manual_instance_id:value.auto_instance_id);
 memset(value.password,0,sizeof(value.password));memset(value.world_password,0,sizeof(value.world_password));return matches;
#else
 ARG_UNUSED(index);ARG_UNUSED(instance);return true;
#endif
}
int xd_client_prepare_dual(struct xm_zclient *client,const char *control,const char *input,const char *perception)
{
	return admit() ? xd_io_start_dual(client,control,input,perception) : -ESHUTDOWN;
}
const char *xd_vm_id(void) { return cycle_inputs.vm_id; }
int xd_command_input(const char *type)
{
	command_valid_until=0;
	if (!strcmp(type,"drive.input")) { command_input=&cycle_inputs.input; }
	else if (!strcmp(type,"perception.result") && cycle_inputs.dual_mode) { command_input=&cycle_inputs.perception; }
	else if (!strcmp(type,"world.track") && cycle_inputs.dual_mode) { command_input=NULL; }
	else { return -EINVAL; }
	return 0;
}
int xd_command_bound(int64_t valid_until_ms){
 if(valid_until_ms<=0)return -EINVAL;
 command_valid_until=valid_until_ms;return 0;
}
int xd_command_bound_us(uint64_t original_valid_until_guest_us){
 /* xdiag counter-us and k_uptime ms are different clocks. Bracket the actual
  * conversion; before+floor(remaining) cannot make the original E later. */
 int64_t before=k_uptime_get();uint64_t counter;
 int rc=xdiag_clock_us(&counter);if(rc)return rc;
 int64_t after=k_uptime_get();
 if(before<0||after<before)return -ERANGE;
 if(original_valid_until_guest_us<=counter)return -ETIME;
 uint64_t remaining=(original_valid_until_guest_us-counter)/1000;
 if(remaining>(uint64_t)(INT64_MAX-before))return -EOVERFLOW;
 int64_t bound=before+(int64_t)remaining;
 if(bound<=after)return -ETIME;
 return xd_command_bound(bound);
}
int xd_publish(struct xm_zclient *client,const char *topic,const char *type,const char *payload)
{
	ARG_UNUSED(client);
	return admit() ? xd_io_report(topic,type,payload,current.completed,&cycle_inputs) : -ESHUTDOWN;
}
int xd_poll(struct xm_zclient *client,int32_t timeout_ms)
{
	ARG_UNUSED(client); ARG_UNUSED(timeout_ms);
	return admit() ? -EPERM : -ESHUTDOWN;
}

int xd_iteration_begin(void)
{
	memset(&current, 0, sizeof(current));
	current.iteration_begin = now_us();
 command_input=&cycle_inputs.input;command_valid_until=0;
	int rc = xd_io_snapshot(&cycle_inputs);
	cycle_inputs_status = rc;
	if (rc) {
		/* Do not wait or mix generations. Missing input follows the existing
		 * algorithm's idle/stop rules; a busy snapshot is not fresh data. */
		memset(&cycle_inputs,0,sizeof(cycle_inputs));
		cycle_inputs.perception.error = cycle_inputs.input.error = cycle_inputs.control.error = rc;
	}
	return current.iteration_begin == UINT64_MAX ? -EIO : 0;
}
void xd_inputs_copy(struct xd_inputs *out) { if (out) { *out = cycle_inputs; } }
int xd_inputs_status(void) { return cycle_inputs_status; }
int xd_publish_completed(const char *topic,const char *type,const char *payload,
 uint64_t progress,int64_t produced_ms,const struct xd_inputs *source)
{
 return admit() ? xd_io_report_at(topic,type,payload,progress,source,produced_ms) : -ESHUTDOWN;
}
int64_t xd_cleanup_deadline_ms(void) { return cleanup_end(); }
int xd_publish_supervision(const char *topic,const char *payload,uint64_t check,int64_t at,const struct xd_inputs *context)
{
 int rc=admit()?xd_io_report_at(topic,"app.lifecycle",payload,check,context,at):-ESHUTDOWN;
#if defined(CONFIG_XENOVA_DRIVE_WARM)
 if(rc){
  atomic_inc(&lifecycle_rejected);
  (void)atomic_cas(&lifecycle_first_rejection,0,rc);
 }else atomic_inc(&lifecycle_queued);
#endif
 return rc;
}
void xd_app_cleanup_error(int error)
{
 if(error){atomic_set(&app_close_error,error);(void)xdiag_error("drive-app-cleanup",error);}
}
int xd_completed_work(uint64_t release,uint64_t begin,uint64_t end,
 uint64_t iteration_begin,int64_t finished_ms,const struct xd_inputs *source)
{
 uint64_t observed=now_us();
 if(observed==UINT64_MAX)return -EIO;
 if(!source||iteration_begin>begin||begin>end||end>observed||release>begin||
    finished_ms<0||finished_ms>k_uptime_get())return -EINVAL;
 k_spinlock_key_t key=k_spin_lock(&loop.lock);
 struct xdiag_timing next=loop.timing;
 int rc=0;
 if(!next.initialized||next.in_flight)rc=-EINVAL;
 else if(release<next.next_release_us)rc=-EALREADY;
 else if(release<next.origin_us||(release-next.origin_us)%next.period_us)rc=-EINVAL;
 uint64_t missing=rc?0:(release-next.next_release_us)/next.period_us;
 if(!rc&&UINT64_MAX-next.skipped_release_count<missing)rc=-EOVERFLOW;
 if(!rc){next.next_release_us=release;next.skipped_release_count+=missing;rc=xdiag_timing_begin(&next,begin);}
 uint64_t misses=next.deadline_miss_count,skips=loop.timing.skipped_release_count;
 if(!rc)rc=xdiag_timing_end(&next,end);
 if(!rc)loop.timing=next;
 k_spin_unlock(&loop.lock,key);
 if(rc)return rc;
 memset(&current,0,sizeof(current));
 current.iteration_begin=iteration_begin;current.begin=begin;current.step_end=end;
 current.release=release;current.completed=next.loop_seq;
 current.missed=next.deadline_miss_count-misses;current.skipped=next.skipped_release_count-skips;
 work_finished_ms=finished_ms;
 cycle_inputs=*source;command_input=&cycle_inputs.input;command_valid_until=0;
 return 0;
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
		work_finished_ms = k_uptime_get();
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
		int64_t control_seq, int64_t input_age_ms, uint32_t input_timeout_ms)
{
	current.generation = generation; current.input_seq = input_seq; current.control_seq = control_seq;
	current.input_age_ms = input_age_ms;
	current.encoded = encoded;
	BUILD_ASSERT(sizeof(float) == sizeof(uint32_t));
	memcpy(&current.v_bits, &v_mps, sizeof(v_mps));
	memcpy(&current.steer_bits, &steer_rad, sizeof(steer_rad));
	current.submit_begin = now_us();
	current.rc = encoded < 0 ? encoded : -ESHUTDOWN;
	ARG_UNUSED(client);
	if (encoded >= 0 && !xd_stopping()) {
		current.attempted = true; /* schema 2: mailbox offer, not socket attempt */
		memset(&candidate,0,sizeof(candidate));
		candidate.id=current.completed; candidate.created_us=current.step_end;
		candidate.queued_us=current.submit_begin;
		candidate.capture_us=current.begin;
		candidate.control_revision=cycle_inputs.control_revision;
		candidate.connection_revision=cycle_inputs.connection_revision;
		candidate.app_instance[0]=0;
#if defined(CONFIG_XENOVA_DRIVE_WARM)
		struct xm_topic_info info;xm_topic_parse(topic,&info);
		strcpy(candidate.app_instance,cycle_inputs.app_instances[!strcmp(info.app_id,"manual")?1:0]);
#endif
		candidate.expires_ms=work_finished_ms+CONFIG_XENOVA_DRIVE_COMMAND_MAX_AGE_MS;
		if(command_valid_until)candidate.expires_ms=MIN(candidate.expires_ms,command_valid_until);
		if ((v_mps!=0.0f||steer_rad!=0.0f) && command_input && !command_input->error)
			candidate.expires_ms=MIN(candidate.expires_ms,command_input->received_ms+input_timeout_ms+1);
		struct xm_drive_command2 modern;
		struct xm_drive_command3 clocked;
		int source_rc=0;
		struct xm_span command_span={json,(uint16_t)strlen(json)};
		int parsed=xm_drive_command3_parse(command_span,&clocked);
		if(!parsed)modern=clocked.command;
		else parsed=xm_drive_command2_parse(command_span,&modern);
		if(!parsed&&
		   !strcmp(modern.producer_role,"MANUAL")&&strcmp(modern.command_kind,"STOP")){
			candidate.modern_manual=true;
			if(!command_input||command_input->error)source_rc=-ENODATA;
			else source_rc=xm_manual_input_parse((struct xm_span){command_input->payload,(uint16_t)command_input->length},&candidate.manual_source);
			if(!source_rc&&(!command_valid_until||!candidate.manual_source.valid||
			   strcmp(modern.driver_session,candidate.manual_source.session)||modern.driver_action_seq!=candidate.manual_source.action||
			   modern.driver_state_seq!=candidate.manual_source.state||
			   modern.valid_until_pi_us>candidate.manual_source.expires_pi_us))source_rc=-EXDEV;
		}
		if(source_rc)current.rc=source_rc;
		else if (strlen(topic)>=sizeof(candidate.topic)||strlen(json)>=sizeof(candidate.json)) current.rc=-EMSGSIZE;
		else { strcpy(candidate.topic,topic);strcpy(candidate.json,json);current.rc=xd_io_command(&candidate); }
	}
	current.submit_end = current.rc ? now_us() : candidate.queued_us;
}

static void increment(uint64_t *value)
{
	if (*value == UINT64_MAX) { counter_saturated = true; }
	else { ++*value; }
}

static void seal_transmissions(void)
{
	/* Called only with capture_lock held. No candidate can newly enter a
	 * completed calculation window after its final iteration is recorded. */
	if (capture_complete && capture_admitted == capture_terminal) { transmissions_complete = true; }
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
#if defined(CONFIG_XENOVA_DRIVE_RECORDER)
	if (capture_end && current.begin >= capture_start && current.begin < capture_end) {
		if (current.attempted && !current.rc) { increment(&capture_admitted); }
		if (count < ARRAY_SIZE(rows)) { rows[count++] = current; } else { increment(&overflow); }
	}
#endif
	if (capture_end && current.iteration_end >= capture_end) { capture_complete = true; }
	seal_transmissions();
	k_spin_unlock(&capture_lock, key);
}

uint64_t xd_next_release_us(void) { return loop.timing.next_release_us; }
uint64_t xd_completion_deadline_us(void)
{
 return loop.timing.in_flight&&UINT64_MAX-loop.timing.release_us>=loop.timing.deadline_us?
  loop.timing.release_us+loop.timing.deadline_us:0;
}
bool xd_step_deadline_missed(void){return current.missed!=0;}
void xd_health_record(const struct xdh_health *automatic,const struct xdh_health *manual)
{
 if(!automatic||!manual)return;
 k_spinlock_key_t key=k_spin_lock(&capture_lock);
 auto_health=*automatic;manual_health=*manual;
 for(unsigned i=0;i<2;i++){health_observed[i]=true;health_completed[i]=current.completed;health_step_end[i]=current.step_end;}
 k_spin_unlock(&capture_lock,key);
}
void xd_health_task_record(unsigned task,const struct xdh_health *value)
{
 if(task>=2||!value)return;
 k_spinlock_key_t key=k_spin_lock(&capture_lock);
 if(task)manual_health=*value;else auto_health=*value;
 health_observed[task]=true;health_completed[task]=current.completed;health_step_end[task]=current.step_end;
 k_spin_unlock(&capture_lock,key);
}
void xd_health_task_clear(unsigned task)
{
 if(task>=2)return;
 k_spinlock_key_t key=k_spin_lock(&capture_lock);health_observed[task]=false;k_spin_unlock(&capture_lock,key);
}

void xd_latest(struct xm_zclient *client, const char *topic, const char *type,
		struct xm_span *payload, int64_t *age_ms, int64_t *seq)
{
	*payload = (struct xm_span){0}; *age_ms = 0; *seq = -1;
	ARG_UNUSED(client); ARG_UNUSED(topic);
	const struct xd_received *r = !strcmp(type,"vehicle.control") ? &cycle_inputs.control :
  (cycle_inputs.dual_mode && !strcmp(type,"perception.result") ? &cycle_inputs.perception : &cycle_inputs.input);
	if (!r->error && r->length) {
		*payload=(struct xm_span){r->payload,r->length}; *seq=r->seq;
		*age_ms=k_uptime_get()-r->received_ms;
	}
}

void xd_delivery_record(const struct xd_delivery *d)
{
#if defined(CONFIG_XENOVA_DRIVE_RECORDER)
	k_spinlock_key_t key=k_spin_lock(&capture_lock);
	if(capture_end&&d->capture_us>=capture_start&&d->capture_us<capture_end) {
		if (transmissions_complete) { increment(&delivery_overflow); }
		else {
			increment(&capture_terminal);
			if(delivery_count<ARRAY_SIZE(deliveries))deliveries[delivery_count++]=*d;
			else increment(&delivery_overflow);
			seal_transmissions();
		}
	}
	k_spin_unlock(&capture_lock,key);
#else
	ARG_UNUSED(d);
#endif
}

#define emit(sh, ...) do { if (sh) { shell_print(sh, __VA_ARGS__); } else { printf(__VA_ARGS__); printf("\n"); } } while (0)

#if defined(CONFIG_XENOVA_DRIVE_RECORDER)
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
#endif

#if defined(CONFIG_XENOVA_RUNTIME) && defined(CONFIG_XENOVA_NETFRONT)
static int netfront_snapshot(const struct shell *sh)
{
	struct xnet_status status;
	struct xnet_waits waits;
	int rc = xnet_status_get(&status);
	if (rc) { return rc; }
	rc = xnet_waits_get(&waits);
	if (rc) { return rc; }

	/* Two bounded copies, not an atomic view of traffic in flight. */
	emit(sh, "XD_NETFRONT {\"started\":%s,\"connected\":%s,\"stopping\":%s,"
	     "\"backend_observation\":%d,\"backend_error\":%d,\"observation_error\":%d,"
	     "\"first_observation_error\":%d,\"backend_wait_stage\":%d,"
	     "\"worker_source\":\"%s\",\"worker_error\":%d}",
	     status.started ? "true" : "false", status.connected ? "true" : "false",
	     status.stopping ? "true" : "false", status.backend_observation, status.backend_error,
	     status.observation_error, status.first_observation_error, status.backend_wait_stage,
	     status.worker_failure_source ? status.worker_failure_source : "none", status.worker_error);
	emit(sh, "XD_NETFRONT_WAITS {\"packet_clock\":\"%s\",\"packet_polls\":\"%" PRIu64
	     "\",\"packet_last_poll_ms\":\"%" PRIu64 "\",\"packet_gap_max_us\":\"%" PRIu64
	     "\",\"packet_lock_timeouts\":\"%" PRIu64 "\",\"packet_lock_first_error\":%d,"
	     "\"packet_lock_hold_max_us\":\"%" PRIu64 "\",\"store_unavailable\":\"%" PRIu64
	     "\",\"store_recoveries\":\"%" PRIu64 "\",\"last_backend_success_ms\":\"%" PRIu64 "\"}",
	     waits.cycle_clock ? "cycles" : "ticks", waits.packet_polls, waits.packet_last_poll_ms,
	     waits.packet_gap_max_us, waits.packet_lock_timeouts, waits.packet_lock_first_error,
	     waits.packet_lock_hold_max_us, waits.store_unavailable, waits.store_recoveries,
	     waits.last_backend_success_ms);
	return 0;
}
#endif

static int drive_cmd_inner(const struct shell *sh, size_t argc, char **argv)
{
#if defined(CONFIG_XENOVA_RUNTIME) && defined(CONFIG_XENOVA_NETFRONT)
	if (argc == 2 && !strcmp(argv[1], "netfront")) { return netfront_snapshot(sh); }
#endif
 if(argc==2&&!strcmp(argv[1],"health")){
  struct xdh_health health[2];
  k_spinlock_key_t key=k_spin_lock(&capture_lock);
  bool available[2]={health_observed[0],health_observed[1]};health[0]=auto_health;health[1]=manual_health;
  uint64_t completed[2]={health_completed[0],health_completed[1]},step_end[2]={health_step_end[0],health_step_end[1]};
  k_spin_unlock(&capture_lock,key);
  if(!available[0]&&!available[1])return -ENODATA;
  for(size_t i=0;i<ARRAY_SIZE(health);i++){
   if(!available[i]){emit(sh,"XD_HEALTH {\"mode\":\"%s\",\"rc\":%d}",i?"MANUAL":"AUTO",-ENODATA);continue;}
   char cpu[16]="null";
   if(health[i].cpu_valid)snprintf(cpu,sizeof(cpu),"%u",health[i].cpu_percent_milli);
   emit(sh,"XD_HEALTH {\"mode\":\"%s\",\"failure\":%u,\"restrict_function\":%s,\"warnings\":%u,\"work_rc\":%d,\"cpu_rc\":%d,\"cpu_valid\":%s,\"cpu_percent_milli\":%s,\"resource_sample\":\"%" PRIu64 "\",\"completed\":\"%" PRIu64 "\",\"step_end_us\":\"%" PRIu64 "\",\"stopping\":%s}",
    i?"MANUAL":"AUTO",(unsigned)health[i].failure,health[i].restrict_function?"true":"false",
    health[i].warnings,health[i].work_rc,health[i].cpu_rc,health[i].cpu_valid?"true":"false",
    cpu,health[i].resource_sample,completed[i],step_end[i],xd_stopping()?"true":"false");
  }
  return 0;
 }
	bool sends = argc >= 2 && !strcmp(argv[1], "transmissions");
	bool records = sends || (argc >= 2 && !strcmp(argv[1], "records"));
	if (!IS_ENABLED(CONFIG_XENOVA_DRIVE_RECORDER) &&
	    (records || (argc >= 2 && !strcmp(argv[1], "capture")))) { return -ENOTSUP; }
	if (argc == 4 && !strcmp(argv[1], "capture")) {
		char *tail;
		unsigned long delay = strtoul(argv[2], &tail, 10); if (!*argv[2] || *tail || delay > 60000) { return -EINVAL; }
		unsigned long duration = strtoul(argv[3], &tail, 10); if (!*argv[3] || *tail || !duration || duration > 240000) { return -EINVAL; }
		uint64_t now = now_us(); if (now == UINT64_MAX) { return -EIO; }
		k_spinlock_key_t key = k_spin_lock(&capture_lock);
		if (capture_end && !transmissions_complete) { k_spin_unlock(&capture_lock, key); return -EBUSY; }
		capture_start = now + delay * 1000U; capture_end = capture_start + duration * 1000U;
		count = delivery_count = 0; overflow = delivery_overflow = 0;
		capture_complete = transmissions_complete = false;
		capture_admitted = capture_terminal = 0;
		k_spin_unlock(&capture_lock, key);
	} else if (argc != 1 && !(records && argc <= 4)) { return -EINVAL; }
	/* Snapshot metadata under the same lock as capture. No long dump holds it. */
	k_spinlock_key_t key = k_spin_lock(&capture_lock);
	size_t size = sends ? delivery_count : count; uint64_t lost = overflow, first = capture_start, end = capture_end;
	bool complete = capture_complete, sends_complete = transmissions_complete, saturated = counter_saturated;
	size_t delivery_size = delivery_count; uint64_t delivery_lost = delivery_overflow;
	uint64_t gen = generated, attempts = attempted, accepts = accepted, failures = failed, drops = dropped;
	struct xm_zclient *client = app_client;
	k_spin_unlock(&capture_lock, key);
	if (!client) { return -ENODATA; }
	if (records && (!complete || !sends_complete)) { return -EBUSY; }
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
	if ((!records || !offset) && !sends) {
		emit(sh, "XD_SUMMARY {\"schema_version\":2,\"app\":\"%s\",\"generated\":\"%" PRIu64 "\",\"mailbox_offered\":\"%" PRIu64 "\",\"mailbox_accepted\":\"%" PRIu64 "\",\"mailbox_rejected\":\"%" PRIu64 "\",\"not_offered\":\"%" PRIu64 "\",\"capture_start_us\":\"%" PRIu64 "\",\"capture_end_us\":\"%" PRIu64 "\",\"rows\":%zu,\"overflow\":\"%" PRIu64 "\",\"capture_complete\":%s,\"transmissions_complete\":%s,\"counter_saturated\":%s,\"clock_rc\":%d,\"task_rc\":%d}",
			app, gen, attempts, accepts, failures, drops, first, end, size, lost,
			complete ? "true" : "false", sends_complete ? "true" : "false", saturated ? "true" : "false", (int)atomic_get(&clock_rc), (int)atomic_get(&task_rc));
	}
	if (records) {
		if (!offset && !sends) {
			emit(sh, "XD_SESSION {\"vm_id\":\"%s\",\"app_id\":\"%s\",\"boot_id\":\"%s\",\"agent_instance_id\":\"%s\",\"execution_id\":\"%s\"}",
				app_bootstrap.identity.vm_id, app, client->id.boot_id, client->id.instance_id,
				app_bootstrap.identity.execution_id);
		}
		size_t next = MIN(size, offset + limit);
#if defined(CONFIG_XENOVA_DRIVE_RECORDER)
		for (size_t i = offset; i < next; ++i) {
			if (!sends) { output_row(sh, &rows[i]); continue; }
			const struct xd_delivery *t=&deliveries[i];
			emit(sh,"XD_SEND [\"%" PRIu64 "\",\"%" PRIu64 "\",\"%" PRIu64 "\",\"%" PRIu64 "\",\"%" PRIu64 "\",\"%" PRIu64 "\",%u,%u,%d,%d]",
			 t->id,t->created_us,t->queued_us,t->taken_us,t->send_begin_us,t->send_end_us,t->message,t->topic_seq,t->rc,t->disposition);
		}
#endif
		if (sh) { emit(sh, "XD_PAGE {\"offset\":%zu,\"next\":%zu,\"total\":%zu}", offset, next, size); }
	}
	struct xd_io_stats io;
	int io_status=xd_io_stats_get(&io);
	if ((!records || (sends && !offset)) && !io_status) {
		emit(sh,"XD_IO {\"enqueued\":\"%" PRIu64 "\",\"replaced\":\"%" PRIu64 "\",\"expired\":\"%" PRIu64 "\",\"context_dropped\":\"%" PRIu64 "\",\"stopped\":\"%" PRIu64 "\",\"attempted\":\"%" PRIu64 "\",\"sent\":\"%" PRIu64 "\",\"failed\":\"%" PRIu64 "\",\"event_dropped\":\"%" PRIu64 "\",\"heartbeat_replaced\":\"%" PRIu64 "\",\"heartbeat_dropped\":\"%" PRIu64 "\",\"heartbeat_expired\":\"%" PRIu64 "\",\"readiness_replaced\":\"%" PRIu64 "\",\"readiness_dropped\":\"%" PRIu64 "\",\"readiness_expired\":\"%" PRIu64 "\",\"prepare_max_us\":\"%" PRIu64 "\",\"poll_max_us\":\"%" PRIu64 "\",\"exchange_max_us\":\"%" PRIu64 "\",\"delivery_rows\":%zu,\"delivery_overflow\":\"%" PRIu64 "\",\"transmissions_complete\":%s,\"close_error\":%d}",
		 io.enqueued,io.replaced,io.expired,io.context_dropped,io.stopped,io.attempted,io.sent,io.failed,io.event_dropped,io.heartbeat_replaced,io.heartbeat_dropped,io.heartbeat_expired,io.readiness_replaced,io.readiness_dropped,io.readiness_expired,io.prepare_max_us,io.poll_max_us,io.exchange_max_us,delivery_size,delivery_lost,sends_complete ? "true" : "false",io.close_error);
#if defined(CONFIG_XENOVA_DRIVE_WARM)
		struct xd_context_status context;
		int context_rc=xd_context_status_get(&context);
		emit(sh,"XD_REPORT {\"queued\":%u,\"queue_rejected\":%u,\"first_queue_rc\":%d,\"sent\":\"%" PRIu64 "\",\"first_send_rc\":%d}",
		     (unsigned)atomic_get(&lifecycle_queued),(unsigned)atomic_get(&lifecycle_rejected),
		     (int)atomic_get(&lifecycle_first_rejection),io.lifecycle_sent,io.lifecycle_first_send_error);
		if(!context_rc){
			emit(sh,"XD_CONTEXT {\"loads\":\"%" PRIu64 "\",\"failures\":\"%" PRIu64 "\",\"first_rc\":%d,\"last_rc\":%d,\"available\":%s}",
			     context.loads,context.failures,context.first_error,context.last_error,context.available?"true":"false");
		}
#endif
		if (!io.stack_error) {
			emit(sh,"XD_CLOCK {\"frequency_hz\":%u,\"io_clock_rc\":%d,\"snapshot_busy\":\"%" PRIu64 "\",\"stack_rc\":0,\"stack_unused_bytes\":%zu}",
			 sys_clock_hw_cycles_per_sec(),io.clock_error,io.snapshot_busy,io.stack_unused_bytes);
		} else {
			emit(sh,"XD_CLOCK {\"frequency_hz\":%u,\"io_clock_rc\":%d,\"snapshot_busy\":\"%" PRIu64 "\",\"stack_rc\":%d,\"stack_unused_bytes\":null}",
			 sys_clock_hw_cycles_per_sec(),io.clock_error,io.snapshot_busy,io.stack_error);
		}
	}
	return io_status;
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
	/* Calculation is quiesced; the communication owner closes its own socket.
	 * Join shares the same cleanup deadline with Publisher/netfront/Store. */
	int64_t io_end = cleanup_end();
	int io_rc = xd_io_join(io_end);
#if defined(CONFIG_XENOVA_DRIVE_WARM)
	int context_rc=xd_context_join(io_end);
	if(context_rc&&!io_rc)io_rc=context_rc;
#endif
	if (io_rc) { atomic_set(&app_close_error, io_rc); }
	if (app_running) {
		atomic_set(&app_quiesced, 1);
		k_sem_give(&app_done);
	}
	int cleanup = atomic_get(&app_close_error);
	if (io_rc) { (void)xdiag_error("drive-io-cleanup",io_rc); }
#if defined(CONFIG_XENOVA_RUNTIME)
	int64_t end = io_end;
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
#if defined(CONFIG_XENOVA_DRIVE_RECORDER)
	k_spinlock_key_t key = k_spin_lock(&capture_lock);
	uint64_t closed = now_us();
	if (capture_end == UINT64_MAX) { capture_end = closed; }
	capture_complete = capture_end && closed >= capture_end;
	seal_transmissions();
	k_spin_unlock(&capture_lock, key);
#endif
	uint64_t before = now_us();
	int rc = xt_stop(io_end);
	struct xt_status state;
	int status_rc = xt_status_get(&state);
	printf("XD_STOP {\"rc\":%d,\"status_rc\":%d,\"elapsed_us\":\"%" PRIu64 "\",\"worker_running\":%s}\n",
		rc, status_rc, now_us() - before, !status_rc && state.running ? "true" : "false");
	printf("XD_CONNECTION {\"attempts\":%u,\"last_io_rc\":%d,\"received\":%u}\n",
		client->connection_attempts, client->last_io_rc, client->received);
	/* Native test output is after stopping, never a 50Hz console producer. */
	char *argv[] = {"drive", "records"};
	(void)drive_cmd(NULL, 2, argv);
	argv[1]="transmissions"; (void)drive_cmd(NULL,2,argv);
	argv[1]="health"; (void)drive_cmd(NULL,2,argv);
	fflush(stdout);
	exit(app_result || cleanup || rc || status_rc || state.running ? 1 : 0);
#endif
	return app_result ? app_result : cleanup;
}
void xd_client_stopped(struct xm_zclient *client) { (void)xd_finish(client, 0); }

SHELL_STATIC_SUBCMD_SET_CREATE(xenova_commands,
	SHELL_CMD(diag, NULL, "Local Core snapshot; threads is intrusive", xdiag_shell_cmd),
	SHELL_CMD(telemetry, NULL, "Separate v2 Publisher status/snapshot", xt_shell_cmd),
	SHELL_CMD(drive, NULL, "Local health/netfront; capture <delay_ms> <duration_ms>; records [offset] [1..4] after window", drive_cmd),
	SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(xenova, &xenova_commands, "Xenova common diagnostics", NULL);
