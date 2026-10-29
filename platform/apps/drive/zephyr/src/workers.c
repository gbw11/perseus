/* SPDX-License-Identifier: Apache-2.0 */
/* Separate preemptible AUTO/MANUAL owners. Long callbacks hold no exchange
 * lock. A stuck callback is retained and reported, never forcibly recycled. */
#include "workers.h"
#include <xenova/diagnostics.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <errno.h>
#include <string.h>

struct task {
 struct k_thread thread;
 struct k_sem wake;
 struct k_mutex exchange;
 struct drive_adapter adapter;
 struct drive_job pending, running;
 struct drive_completion result;
 struct xdr_snapshot resources;
 struct drive_task_status status;
};
static struct task tasks[DRIVE_TASK_COUNT];
K_THREAD_STACK_DEFINE(auto_stack, CONFIG_XENOVA_DRIVE_WORKER_STACK_SIZE);
K_THREAD_STACK_DEFINE(manual_stack, CONFIG_XENOVA_DRIVE_WORKER_STACK_SIZE);
K_MUTEX_DEFINE(workers_lifecycle);
static atomic_t stopping, initialized;
static bool attempted;
static int64_t cleanup_deadline;

static bool valid(enum drive_task task) { return (unsigned)task < DRIVE_TASK_COUNT; }

static void run(void *argument, void *unused1, void *unused2)
{
 ARG_UNUSED(unused1); ARG_UNUSED(unused2);
 struct task *t = argument;
 for (;;) {
  k_sem_take(&t->wake, K_FOREVER);
  k_mutex_lock(&t->exchange, K_FOREVER);
  if (atomic_get(&stopping)) { t->status.pending = false; k_mutex_unlock(&t->exchange); break; }
  if (!t->status.pending) { k_mutex_unlock(&t->exchange); continue; }
  t->running = t->pending; t->status.pending = false; t->status.state = DRIVE_TASK_RUNNING;
  k_mutex_unlock(&t->exchange);
  struct drive_completion result = {.sequence=t->running.sequence, .context_revision=t->running.context_revision};
  result.source = t->running;
  int clock_rc = xdiag_clock_us(&result.begin_us);
  int resource_rc = xdiag_resources_try_get(&t->resources);
  int64_t at_ms = k_uptime_get();
  int64_t age = t->running.input_length && at_ms >= t->running.input_received_ms ?
   at_ms - t->running.input_received_ms : -1;
  if (clock_rc) { result.rc = clock_rc; }
  else if (result.begin_us > t->running.deadline_us) { result.rc = -ETIMEDOUT; }
  else if (atomic_get(&stopping)) { result.rc = -ESHUTDOWN; }
  else {
   result.performed = true;
   struct xm_span control = {t->running.control, t->running.control_length};
   struct xm_span input = {t->running.input, t->running.input_length};
   if (!t->running.input_length) { input.p = NULL; }
   result.rc = drive_adapter_run(&t->adapter, control, input, age, &result.sample);
  }
  int end_rc = xdiag_clock_us(&result.end_us);
  result.finished_ms = k_uptime_get();
  result.progress = t->adapter.progress;
  struct xdh_work work = {.state=XDH_COMPLETED, .required=true, .rc=result.rc,
   .clock_rc=clock_rc ? clock_rc : end_rc, .begin_us=result.begin_us, .end_us=result.end_us,
   .deadline_us=t->running.deadline_us};
  (void)xdh_assess(&work, result.end_us, k_uptime_get(), &t->resources, resource_rc, &result.health);
  if (result.health.restrict_function) {
   result.sample.v_mps = result.sample.steer_rad = 0;
   if (!result.sample.reason || strcmp(result.sample.reason, "OUTPUT_INVALID")) result.sample.reason = "CALCULATION_FAILED";
  }
  k_mutex_lock(&t->exchange, K_FOREVER);
  t->result = result; t->status.completed_sequence = result.sequence;
  t->status.state = atomic_get(&stopping) ? DRIVE_TASK_STOPPING : DRIVE_TASK_WAITING;
  k_mutex_unlock(&t->exchange);
 }
 k_mutex_lock(&t->exchange, K_FOREVER);
 t->status.state = DRIVE_TASK_STOPPED;
 k_mutex_unlock(&t->exchange);
}

int drive_workers_start(const struct drive_adapter *automatic, const struct drive_adapter *manual)
{
 if (!automatic || !manual || !automatic->calculate || !manual->calculate) return -EINVAL;
 if (k_mutex_lock(&workers_lifecycle, K_NO_WAIT)) return -EBUSY;
 if (attempted || atomic_get(&stopping)) { k_mutex_unlock(&workers_lifecycle); return -EALREADY; }
 attempted = true;
 const struct drive_adapter *adapters[] = {automatic, manual};
 k_thread_stack_t *stacks[] = {auto_stack, manual_stack};
 const int priorities[] = {CONFIG_XENOVA_DRIVE_AUTO_PRIORITY, CONFIG_XENOVA_DRIVE_MANUAL_PRIORITY};
 int result = 0;
 for (size_t i=0; i<DRIVE_TASK_COUNT; ++i) {
  struct task *t = &tasks[i];
  k_mutex_init(&t->exchange); k_sem_init(&t->wake, 0, 1);
  t->adapter = *adapters[i];
 }
 atomic_set(&initialized, 1);
 for (size_t i=0; i<DRIVE_TASK_COUNT; ++i) {
  if (atomic_get(&stopping)) { result = -ESHUTDOWN; break; }
  struct task *t = &tasks[i];
  k_tid_t id = k_thread_create(&t->thread, stacks[i], K_THREAD_STACK_SIZEOF(auto_stack),
   run, t, NULL, NULL, priorities[i], 0, K_FOREVER);
  if (!id) { result = -ENOMEM; break; }
  t->status.started = true; t->status.state = DRIVE_TASK_WAITING;
  k_thread_name_set(id, i ? "drive-manual" : "drive-auto");
  /* Even when stop raced with creation, start only to observe the closed gate
   * and exit. Never strand an unstarted thread that join would wait forever. */
  k_thread_start(id);
 }
 k_mutex_unlock(&workers_lifecycle);
 if (result || atomic_get(&stopping)) { drive_workers_stop(); return result ? result : -ESHUTDOWN; }
 return 0;
}

int drive_workers_offer(enum drive_task task, const struct drive_job *job)
{
 if (!valid(task) || !job || !job->sequence || !job->context_revision ||
     job->release_us > job->deadline_us || job->control_length > XM_MAX_DRIVING_BYTES ||
     job->input_length > XM_MAX_DRIVING_BYTES) return -EINVAL;
 if (atomic_get(&stopping)) return -ESHUTDOWN;
 if (k_mutex_lock(&workers_lifecycle, K_NO_WAIT)) return -EBUSY;
 struct task *t = &tasks[task];
 if (!t->status.started) { k_mutex_unlock(&workers_lifecycle); return -ENODEV; }
 if (k_mutex_lock(&t->exchange, K_NO_WAIT)) { k_mutex_unlock(&workers_lifecycle); return -EBUSY; }
 int result = 0;
 if (atomic_get(&stopping)) result = -ESHUTDOWN;
 else if (job->sequence <= t->status.accepted_sequence) result = -EALREADY;
 else {
  if (t->status.pending) {
   if (t->status.replaced == UINT64_MAX) result = -EOVERFLOW;
   else ++t->status.replaced;
  }
  if (!result) {
   t->pending = *job; t->status.accepted_sequence = job->sequence; t->status.pending = true;
   k_sem_give(&t->wake);
  }
 }
 k_mutex_unlock(&t->exchange); k_mutex_unlock(&workers_lifecycle);
 return result;
}

int drive_workers_read(enum drive_task task, uint64_t revision, struct drive_completion *out)
{
 if (!valid(task) || !out) return -EINVAL;
 if (k_mutex_lock(&workers_lifecycle, K_NO_WAIT)) return -EBUSY;
 struct task *t = &tasks[task]; int rc = 0;
 if (!t->status.started) rc = -ENODEV;
 else if (k_mutex_lock(&t->exchange, K_NO_WAIT)) rc = -EBUSY;
 else {
  if (!t->status.completed_sequence) rc = -ENODATA;
  else if (t->result.context_revision != revision) rc = -EXDEV;
  else { *out = t->result; }
  k_mutex_unlock(&t->exchange);
 }
 k_mutex_unlock(&workers_lifecycle); return rc;
}

int drive_workers_status(enum drive_task task, struct drive_task_status *out)
{
 if (!valid(task) || !out) return -EINVAL;
 if (k_mutex_lock(&workers_lifecycle, K_NO_WAIT)) return -EBUSY;
 struct task *t = &tasks[task]; int rc = 0;
 if (!t->status.started) { *out = t->status; }
 else if (k_mutex_lock(&t->exchange, K_NO_WAIT)) rc = -EBUSY;
 else { *out = t->status; k_mutex_unlock(&t->exchange); }
 k_mutex_unlock(&workers_lifecycle); return rc;
}

void drive_workers_stop(void)
{
 atomic_set(&stopping, 1);
 /* Semaphores are initialized together before any thread starts. */
 if (!atomic_get(&initialized)) return;
 for (size_t i=0; i<DRIVE_TASK_COUNT; ++i) k_sem_give(&tasks[i].wake);
}
bool drive_workers_stopping(void) { return atomic_get(&stopping) != 0; }

int drive_workers_join(int64_t deadline)
{
 if (deadline <= 0) return -EINVAL;
 drive_workers_stop();
 if (k_mutex_lock(&workers_lifecycle, K_NO_WAIT)) return -EBUSY;
 if (!cleanup_deadline || deadline < cleanup_deadline) cleanup_deadline = deadline;
 int result = 0;
 for (size_t i=0; i<DRIVE_TASK_COUNT; ++i) {
  struct task *t = &tasks[i];
  if (!t->status.started || t->status.joined) continue;
  int64_t left = cleanup_deadline - k_uptime_get();
  int rc = k_thread_join(&t->thread, left > 0 ? K_MSEC(left) : K_NO_WAIT);
  if (!rc) { t->status.joined = true; t->status.state = DRIVE_TASK_STOPPED; }
  else {
   if (!k_mutex_lock(&t->exchange, K_NO_WAIT)) {
    t->status.state = DRIVE_TASK_RESIDUAL; k_mutex_unlock(&t->exchange);
   }
   if (!result) result = -ETIMEDOUT;
  }
 }
 k_mutex_unlock(&workers_lifecycle); return result;
}
