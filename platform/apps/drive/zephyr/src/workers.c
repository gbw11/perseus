/* SPDX-License-Identifier: Apache-2.0 */
/* Separate preemptible AUTO/MANUAL owners. Long callbacks hold no exchange
 * lock. A stuck callback is retained and reported, never forcibly recycled. */
#include "workers.h"
#include <xenova/auto_delay.h>
#include <xenova/diagnostics.h>
#include <zephyr/kernel.h>
#include <zephyr/sys/atomic.h>
#include <errno.h>
#include <string.h>
#include <stdatomic.h>

struct task {
 struct k_thread thread;
 struct k_sem wake;
 struct k_mutex exchange;
 struct drive_adapter adapter;
 struct drive_job pending, running;
 struct drive_completion result;
 struct xdr_snapshot resources;
 struct drive_task_status status;
 atomic_t cancelled;
 int64_t stop_deadline;
 uint64_t restart_from;
 int restart_rc;
 bool running_live;
 atomic_t running_performed;
 /* Boot-lifetime high-water survives joined application restarts. */
 uint64_t memory_peak[3], memory_read_sequence;
};
static struct task tasks[DRIVE_TASK_COUNT];
K_THREAD_STACK_DEFINE(auto_stack, CONFIG_XENOVA_DRIVE_WORKER_STACK_SIZE);
K_THREAD_STACK_DEFINE(manual_stack, CONFIG_XENOVA_DRIVE_WORKER_STACK_SIZE);
K_MUTEX_DEFINE(workers_lifecycle);
static atomic_t stopping, initialized;
static bool attempted;
static int64_t cleanup_deadline;
static atomic_ullong memory_contention;
static void memory_busy(void)
{
 unsigned long long old=atomic_load_explicit(&memory_contention,memory_order_relaxed);
 while(old!=UINT64_MAX&&!atomic_compare_exchange_weak_explicit(&memory_contention,&old,old+1,
       memory_order_relaxed,memory_order_relaxed)) {}
}

static bool valid(enum drive_task task) { return (unsigned)task < DRIVE_TASK_COUNT; }

static void run(void *argument, void *unused1, void *unused2)
{
 ARG_UNUSED(unused1); ARG_UNUSED(unused2);
 struct task *t = argument;
 for (;;) {
  k_sem_take(&t->wake, K_FOREVER);
  k_mutex_lock(&t->exchange, K_FOREVER);
  if (atomic_get(&stopping)||atomic_get(&t->cancelled)) { t->status.pending = false; k_mutex_unlock(&t->exchange); break; }
  if (!t->status.pending) { k_mutex_unlock(&t->exchange); continue; }
  t->running = t->pending; t->status.pending = false; t->status.state = DRIVE_TASK_RUNNING;
  t->running_live=true;atomic_set(&t->running_performed,0);
  t->memory_peak[1]=sizeof(t->running);
  t->status.running_deadline_us=t->running.deadline_us;
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
  else if (atomic_get(&stopping)||atomic_get(&t->cancelled)) { result.rc = -ESHUTDOWN; }
  else {
   result.performed = true;atomic_set(&t->running_performed,1);
   struct xm_span control = {t->running.control, t->running.control_length};
   struct xm_span input = {t->running.input, t->running.input_length};
   if (!t->running.input_length) { input.p = NULL; }
   result.rc = drive_adapter_run_until(&t->adapter, control, input, age,
     &t->running.manual_witness,t->running.app_instance,t->running.deadline_us,&result.sample);
	if (t == &tasks[DRIVE_TASK_AUTO] && drive_auto_delay_active()) {
		drive_auto_delay_apply();
	}
  }
  int end_rc = xdiag_clock_us(&result.end_us);
  result.finished_ms = k_uptime_get();
  result.progress = t->adapter.progress;
  struct xdh_work work = {.state=XDH_COMPLETED, .required=true, .rc=result.rc,
   .clock_rc=clock_rc ? clock_rc : end_rc, .begin_us=result.begin_us, .end_us=result.end_us,
   .deadline_us=t->running.deadline_us};
  (void)xdh_assess(&work, result.end_us, k_uptime_get(), &t->resources, resource_rc, &result.health);
	if (t == &tasks[DRIVE_TASK_AUTO]) {
		(void)drive_auto_delay_observe_deadline(result.end_us > t->running.deadline_us);
	}
  if (result.health.restrict_function) {
   result.sample.v_mps = result.sample.steer_rad = 0;
   if (!result.sample.reason || strcmp(result.sample.reason, "OUTPUT_INVALID")) result.sample.reason = "CALCULATION_FAILED";
  }
  k_mutex_lock(&t->exchange, K_FOREVER);
  t->running_live=false;
  if(atomic_get(&stopping)||atomic_get(&t->cancelled)){
   t->status.pending=false;t->status.state=DRIVE_TASK_STOPPING;
   k_mutex_unlock(&t->exchange);break;
  }
  t->result = result; t->status.completed_sequence = result.sequence;
  t->memory_peak[2]=sizeof(t->result);
  t->status.state = atomic_get(&stopping) ? DRIVE_TASK_STOPPING : DRIVE_TASK_WAITING;
  k_mutex_unlock(&t->exchange);
 }
 k_mutex_lock(&t->exchange, K_FOREVER);
 t->status.state = DRIVE_TASK_STOPPED;
 k_mutex_unlock(&t->exchange);
}

int drive_workers_start(const struct drive_adapter *automatic, const struct drive_adapter *manual)
{
 if (!automatic || !manual || (!automatic->calculate&&!automatic->calculate_until) || (!manual->calculate&&!manual->calculate_until)) return -EINVAL;
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
  t->status.lifetime=1;
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
 if (k_mutex_lock(&workers_lifecycle, K_NO_WAIT)) { if(task==DRIVE_TASK_MANUAL)memory_busy();return -EBUSY; }
 struct task *t = &tasks[task];
 if (!t->status.started) { k_mutex_unlock(&workers_lifecycle); return -ENODEV; }
 if (k_mutex_lock(&t->exchange, K_NO_WAIT)) { if(task==DRIVE_TASK_MANUAL)memory_busy();k_mutex_unlock(&workers_lifecycle);return -EBUSY; }
 int result = 0;
 if (atomic_get(&stopping)) result = -ESHUTDOWN;
 else if (atomic_get(&t->cancelled)) result = -ESHUTDOWN;
 else if (job->lifetime!=t->status.lifetime && (job->lifetime||t->status.lifetime!=1)) result=-EXDEV;
 else if (job->sequence <= t->status.accepted_sequence) result = -EALREADY;
 else {
  if (t->status.pending) {
   if (t->status.replaced == UINT64_MAX) result = -EOVERFLOW;
   else ++t->status.replaced;
  }
  if (!result) {
   t->pending = *job;t->pending.lifetime=t->status.lifetime;
   t->memory_peak[0]=sizeof(t->pending);
   t->status.accepted_sequence = job->sequence; t->status.pending = true;
   k_sem_give(&t->wake);
  }
 }
 k_mutex_unlock(&t->exchange); k_mutex_unlock(&workers_lifecycle);
 return result;
}

int drive_workers_read(enum drive_task task, uint64_t revision, struct drive_completion *out)
{
 if (!valid(task) || !out) return -EINVAL;
 if (k_mutex_lock(&workers_lifecycle, K_NO_WAIT)) { if(task==DRIVE_TASK_MANUAL)memory_busy();return -EBUSY; }
 struct task *t = &tasks[task]; int rc = 0;
 if (!t->status.started) rc = -ENODEV;
 else if (k_mutex_lock(&t->exchange, K_NO_WAIT)) { if(task==DRIVE_TASK_MANUAL)memory_busy();rc=-EBUSY; }
 else {
  if (atomic_get(&t->cancelled)||atomic_get(&stopping)) rc=-ESHUTDOWN;
  else if (!t->status.completed_sequence) rc = -ENODATA;
  else if (t->result.context_revision != revision) rc = -EXDEV;
  else { *out = t->result;t->memory_read_sequence=t->result.sequence; }
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
 if(!rc)out->revoked=atomic_get(&t->cancelled)||atomic_get(&stopping);
 k_mutex_unlock(&workers_lifecycle); return rc;
}

static void memory_job(struct xdr_app_buffer *out,const struct drive_job *job,
                       uint8_t phase,uint64_t now)
{
 out->phase=phase;out->sequence=job->sequence;out->source_ms=job->input_received_ms;
 out->lifetime=job->lifetime;out->context_revision=job->context_revision;
 out->connection_revision=job->connection_revision;
 out->content_bytes=job->control_length+job->input_length;
 out->input_status=job->input_length?XDR_INPUT_UNCLASSIFIED:XDR_INPUT_MISSING;
 if(now>job->deadline_us)out->input_status=XDR_INPUT_EXPIRED;
}
int drive_workers_memory_read(struct xdr_app_region out[3],struct xdr_app_buffer detail[3])
{
 if(!out||!detail)return -EINVAL;
 uint64_t now;int rc=xdiag_clock_us(&now);if(rc)return rc;
 if(k_mutex_lock(&workers_lifecycle,K_NO_WAIT))return -EBUSY;
 if(!atomic_get(&initialized)){k_mutex_unlock(&workers_lifecycle);return -ENODEV;}
 struct task *t=&tasks[DRIVE_TASK_MANUAL];
 if(k_mutex_lock(&t->exchange,K_NO_WAIT)){
  k_mutex_unlock(&workers_lifecycle);return -EBUSY;
 }
 memset(out,0,3*sizeof(*out));memset(detail,0,3*sizeof(*detail));
 const uint64_t sizes[]={sizeof(t->pending),sizeof(t->running),sizeof(t->result)};
 bool occupied[]={t->status.pending,t->running_live,t->status.completed_sequence!=0};
 for(unsigned i=0;i<3;i++){
  out[i]=(struct xdr_app_region){.reserved=sizes[i],.capacity=sizes[i],
   .used=occupied[i]?sizes[i]:0,.peak=t->memory_peak[i],
   .contention=atomic_load_explicit(&memory_contention,memory_order_relaxed)};
  detail[i].revoked=atomic_get(&stopping)||atomic_get(&t->cancelled);
  detail[i].input_status=XDR_INPUT_MISSING;
  detail[i].source_ms=-1; /* Empty storage has no source timestamp. */
 }
 if(occupied[0])memory_job(&detail[0],&t->pending,XDR_BUFFER_PENDING,now);
 if(occupied[1]){
  memory_job(&detail[1],&t->running,XDR_BUFFER_RUNNING,now);
  detail[1].performed=atomic_get(&t->running_performed)!=0;
 }
 if(occupied[2]){
  memory_job(&detail[2],&t->result.source,XDR_BUFFER_RETAINED,now);
  detail[2].content_bytes=sizeof(t->result);detail[2].source_rc=t->result.rc;
  detail[2].performed=t->result.performed;detail[2].read_sequence=t->memory_read_sequence;
 }
 k_mutex_unlock(&t->exchange);k_mutex_unlock(&workers_lifecycle);return 0;
}

void drive_workers_stop(void)
{
 atomic_set(&stopping, 1);
 /* Semaphores are initialized together before any thread starts. */
 if (!atomic_get(&initialized)) return;
 for (size_t i=0; i<DRIVE_TASK_COUNT; ++i){atomic_set(&tasks[i].cancelled,1);k_sem_give(&tasks[i].wake);}
}
bool drive_workers_stopping(void) { return atomic_get(&stopping) != 0; }
bool drive_task_stopping(enum drive_task task)
{return !valid(task)||atomic_get(&stopping)||atomic_get(&tasks[task].cancelled);}

int drive_task_cancel(enum drive_task task,uint64_t lifetime,int64_t deadline)
{
 if(!valid(task)||!lifetime||deadline<=0)return -EINVAL;
 if(k_mutex_lock(&workers_lifecycle,K_NO_WAIT))return -EBUSY;
 struct task *t=&tasks[task];int rc=0;
 if(!atomic_get(&initialized))rc=-ENODEV;
 else if(t->status.lifetime!=lifetime)rc=-EXDEV;
 else{
  /* Revocation does not depend on callback return or queue capacity. */
  atomic_set(&t->cancelled,1);
  if(!t->stop_deadline||deadline<t->stop_deadline)t->stop_deadline=deadline;
  k_sem_give(&t->wake);
 }
 k_mutex_unlock(&workers_lifecycle);return rc;
}

int drive_task_join(enum drive_task task,uint64_t lifetime,int64_t deadline)
{
 int rc=drive_task_cancel(task,lifetime,deadline);if(rc)return rc;
 if(k_mutex_lock(&workers_lifecycle,K_NO_WAIT))return -EBUSY;
 struct task *t=&tasks[task];
 if(t->status.lifetime!=lifetime){k_mutex_unlock(&workers_lifecycle);return -EXDEV;}
 if(!t->status.started||t->status.joined){k_mutex_unlock(&workers_lifecycle);return 0;}
 if(t->status.joining){k_mutex_unlock(&workers_lifecycle);return -EBUSY;}
 t->status.joining=true;
 int64_t left=t->stop_deadline-k_uptime_get();
 k_mutex_unlock(&workers_lifecycle);
 rc=k_thread_join(&t->thread,left>0?K_MSEC(left):K_NO_WAIT);
 /* No long operation holds workers_lifecycle. Do not recycle the thread
  * while another observer is joining its old kernel lifetime. */
 k_mutex_lock(&workers_lifecycle,K_FOREVER);
 t->status.joining=false;
 if(!rc){t->status.joined=true;t->status.state=DRIVE_TASK_STOPPED;}
 else if(!k_mutex_lock(&t->exchange,K_NO_WAIT)){
  t->status.state=DRIVE_TASK_RESIDUAL;k_mutex_unlock(&t->exchange);
 }
 k_mutex_unlock(&workers_lifecycle);return rc?-ETIMEDOUT:0;
}

int drive_task_join_poll(enum drive_task task,uint64_t lifetime)
{
 if(!valid(task)||!lifetime)return -EINVAL;
 if(k_mutex_lock(&workers_lifecycle,K_NO_WAIT))return -EBUSY;
 struct task *t=&tasks[task];int rc=0;
 if(t->status.lifetime!=lifetime)rc=-EXDEV;
 else if(!atomic_get(&t->cancelled)||!t->stop_deadline)rc=-EINVAL;
 else if(!t->status.started||t->status.joined){k_mutex_unlock(&workers_lifecycle);return 0;}
 else if(t->status.joining)rc=-EBUSY;
 if(rc){k_mutex_unlock(&workers_lifecycle);return rc;}
 t->status.joining=true;k_mutex_unlock(&workers_lifecycle);
 rc=k_thread_join(&t->thread,K_NO_WAIT);
 k_mutex_lock(&workers_lifecycle,K_FOREVER);t->status.joining=false;
 if(!rc){t->status.joined=true;t->status.state=DRIVE_TASK_STOPPED;}
 else if(k_uptime_get()>=t->stop_deadline){
  k_mutex_lock(&t->exchange,K_FOREVER);t->status.state=DRIVE_TASK_RESIDUAL;k_mutex_unlock(&t->exchange);rc=-ETIMEDOUT;
 }else rc=-EAGAIN;
 k_mutex_unlock(&workers_lifecycle);return rc;
}

int drive_task_restart(enum drive_task task,uint64_t lifetime,const struct drive_adapter *fresh)
{
 if(!valid(task)||!lifetime||!fresh||(!fresh->calculate&&!fresh->calculate_until)||fresh->progress)return -EINVAL;
 if(k_mutex_lock(&workers_lifecycle,K_NO_WAIT))return -EBUSY;
 struct task *t=&tasks[task];int rc=0;
 if(atomic_get(&stopping))rc=-ESHUTDOWN;
 else if(t->status.lifetime!=lifetime){
  rc=t->restart_from==lifetime&&t->status.lifetime==lifetime+1&&
   t->adapter.calculate==fresh->calculate&&t->adapter.calculate_until==fresh->calculate_until&&t->adapter.context==fresh->context?t->restart_rc:-EXDEV;
 }else if(!atomic_get(&t->cancelled)||(t->status.started&&!t->status.joined)||t->status.joining)rc=-EBUSY;
 else if(lifetime==UINT64_MAX)rc=-EOVERFLOW;
 else{
  memset(&t->pending,0,sizeof(t->pending));memset(&t->running,0,sizeof(t->running));
  memset(&t->result,0,sizeof(t->result));memset(&t->resources,0,sizeof(t->resources));
  memset(&t->status,0,sizeof(t->status));t->status.lifetime=lifetime+1;
  t->running_live=false;atomic_set(&t->running_performed,0);t->memory_read_sequence=0;
  t->adapter=*fresh;t->stop_deadline=0;t->restart_from=lifetime;
  k_sem_reset(&t->wake);atomic_set(&t->cancelled,0);
  k_thread_stack_t *stack=task==DRIVE_TASK_AUTO?auto_stack:manual_stack;
  int priority=task==DRIVE_TASK_AUTO?CONFIG_XENOVA_DRIVE_AUTO_PRIORITY:CONFIG_XENOVA_DRIVE_MANUAL_PRIORITY;
  k_tid_t id=k_thread_create(&t->thread,stack,K_THREAD_STACK_SIZEOF(auto_stack),run,t,NULL,NULL,
   priority,0,K_FOREVER);
  if(!id){rc=-ENOMEM;atomic_set(&t->cancelled,1);t->status.state=DRIVE_TASK_STOPPED;}
  else{
   t->status.started=true;t->status.state=DRIVE_TASK_WAITING;
   k_thread_name_set(id,task==DRIVE_TASK_AUTO?"drive-auto":"drive-manual");k_thread_start(id);
  }
  t->restart_rc=rc;
 }
 k_mutex_unlock(&workers_lifecycle);return rc;
}

int drive_workers_join(int64_t deadline)
{
 if (deadline <= 0) return -EINVAL;
 drive_workers_stop();
 if (k_mutex_lock(&workers_lifecycle, K_NO_WAIT)) return -EBUSY;
 if (!cleanup_deadline || deadline < cleanup_deadline) cleanup_deadline = deadline;
 deadline=cleanup_deadline;
 k_mutex_unlock(&workers_lifecycle);
 int result = 0;
 for (size_t i=0; i<DRIVE_TASK_COUNT; ++i) {
  struct drive_task_status status;int rc=drive_workers_status(i,&status);
  if(!rc&&status.started)rc=drive_task_join(i,status.lifetime,deadline);
  if(rc&&!result)result=rc;
 }
 return result;
}
