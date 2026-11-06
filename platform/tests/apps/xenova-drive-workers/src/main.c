/* Actual kernel owners and production adapters; only callback delay/thread
 * creation failure are injected. No broker, Manager, Xen or mock drive app. */
#include <zephyr/kernel.h>
#include <xenova/diagnostics.h>
#include "workers.h"
#include "auto_delay.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if !defined(CONFIG_ARCH_POSIX)
/* The fixed prebuilt picolibc omits source arguments in its assert helper. */
#undef assert
#define assert(condition) __ASSERT((condition), "worker fixture: %s", #condition)
#endif

static struct auto_cfg auto_cfg;
static struct manual_cfg manual_cfg;
static struct drive_adapter automatic, manual;
static struct drive_job job;
static drive_calculate original;
static unsigned auto_calls, creates;
static bool partial, fail_restart;
static bool memory_busy_next;
static k_tid_t memory_observer;
/* Test-only last-stage metadata; no product logging or budget changes. */
static k_tid_t observed_auto;
static atomic_t auto_stage,auto_lock,auto_stage_at[6],auto_lock_at[2];
static void record_auto_stage(unsigned stage)
{
 atomic_set(&auto_stage_at[stage-1],(atomic_val_t)k_uptime_get());
 atomic_set(&auto_stage,(atomic_val_t)stage);
}
int __real_xdiag_resources_try_get(struct xdr_snapshot *);
int __wrap_xdiag_resources_try_get(struct xdr_snapshot *out)
{
 bool observe=k_current_get()==observed_auto;
 if(observe)record_auto_stage(1);
 int rc=__real_xdiag_resources_try_get(out);
 if(observe)record_auto_stage(2);
 return rc;
}
int __real_drive_adapter_run_until(struct drive_adapter *,struct xm_span,struct xm_span,
 int64_t,const struct xd_manual_witness *,const char *,uint64_t,struct drive_sample *);
int __wrap_drive_adapter_run_until(struct drive_adapter *a,struct xm_span control,struct xm_span input,
 int64_t age,const struct xd_manual_witness *w,const char *receiver,uint64_t deadline,struct drive_sample *out)
{
 bool observe=k_current_get()==observed_auto;
 if(observe)record_auto_stage(3);
 int rc=__real_drive_adapter_run_until(a,control,input,age,w,receiver,deadline,out);
 if(observe)record_auto_stage(4);
 return rc;
}
int __real_xdh_assess(const struct xdh_work *,uint64_t,uint64_t,const struct xdr_snapshot *,int,struct xdh_health *);
int __wrap_xdh_assess(const struct xdh_work *work,uint64_t now,uint64_t uptime,
 const struct xdr_snapshot *resource,int rc,struct xdh_health *out)
{
 bool observe=k_current_get()==observed_auto;
 if(observe)record_auto_stage(5);
 int result=__real_xdh_assess(work,now,uptime,resource,rc,out);
 if(observe)record_auto_stage(6);
 return result;
}
int __real_z_impl_k_mutex_lock(struct k_mutex *,k_timeout_t);
int __wrap_z_impl_k_mutex_lock(struct k_mutex *mutex,k_timeout_t timeout)
{
 if(memory_busy_next&&k_current_get()==memory_observer&&K_TIMEOUT_EQ(timeout,K_NO_WAIT)){
  memory_busy_next=false;return -EBUSY;
 }
 bool observe=k_current_get()==observed_auto;
 if(observe){atomic_set(&auto_lock_at[0],(atomic_val_t)k_uptime_get());atomic_set(&auto_lock,1);}
 int rc=__real_z_impl_k_mutex_lock(mutex,timeout);
 if(observe){atomic_set(&auto_lock_at[1],(atomic_val_t)k_uptime_get());atomic_set(&auto_lock,2);}
 return rc;
}
K_SEM_DEFINE(entered, 0, 1);
K_SEM_DEFINE(release, 0, 1);
static int held(void *context, struct xm_span control, struct xm_span input, int64_t age, struct drive_sample *out)
{
 ++auto_calls; k_sem_give(&entered);
 if (auto_calls == 1) k_sem_take(&release, K_FOREVER);
 return original(context, control, input, age, out);
}
k_tid_t __real_z_impl_k_thread_create(struct k_thread *, k_thread_stack_t *, size_t,
 k_thread_entry_t, void *, void *, void *, int, uint32_t, k_timeout_t);
k_tid_t __wrap_z_impl_k_thread_create(struct k_thread *thread, k_thread_stack_t *stack, size_t size,
 k_thread_entry_t entry, void *p1, void *p2, void *p3, int priority, uint32_t options, k_timeout_t delay)
{
 ++creates;
 if ((partial&&creates==2)||(fail_restart&&creates==3)) return NULL;
 k_tid_t id=__real_z_impl_k_thread_create(thread, stack, size, entry, p1, p2, p3, priority, options, delay);
 if(creates==1)observed_auto=id;
 return id;
}
static void prepare(uint64_t sequence, uint64_t revision)
{
 memset(&job, 0, sizeof(job));
 job.sequence=sequence;job.context_revision=revision;
 assert(!xdiag_clock_us(&job.release_us));job.deadline_us=job.release_us+1000000;
 job.input_received_ms=k_uptime_get();
 strcpy(job.control,"{\"generation\":42,\"mode\":\"MANUAL\",\"active_app\":\"manual\",\"active_vm_id\":\"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa\"}");
 strcpy(job.input,"{\"keys\":[\"up\"]}");
 job.control_length=strlen(job.control);job.input_length=strlen(job.input);
}
static void await_result_into(enum drive_task mode,uint64_t revision,uint64_t sequence,
                              struct drive_completion *result)
{
 int64_t end=k_uptime_get()+300;
 while(k_uptime_get()<end){
  int rc=drive_workers_read(mode,revision,result);
  if(!rc&&result->sequence==sequence){assert(result->finished_ms<=end);return;}
  assert(!rc||rc==-ENODATA||rc==-EBUSY);k_sleep(K_MSEC(1));
 }
 /* A preempted observer may resume past end after on-time completion. Read
  * once without waiting; the producer timestamp must still meet the same end. */
 int final_rc=drive_workers_read(mode,revision,result);
 if(!final_rc&&result->sequence==sequence&&result->finished_ms<=end)return;
 printk("WORKER_FINAL_READ rc=%d sequence=%llu finished_ms=%lld end_ms=%lld\n",final_rc,
  (unsigned long long)(!final_rc?result->sequence:0),(long long)(!final_rc?result->finished_ms:-1),
  (long long)end);
 struct drive_task_status status={0};int status_rc=drive_workers_status(mode,&status);
 printk("WORKER_WAIT_FAILURE task=%u sequence=%llu state=%u status_rc=%d pending=%u accepted=%llu completed=%llu revoked=%u auto_stage=%ld auto_lock=%ld now_ms=%lld end_ms=%lld\n",
  (unsigned)mode,(unsigned long long)sequence,(unsigned)status.state,status_rc,status.pending,
  (unsigned long long)status.accepted_sequence,(unsigned long long)status.completed_sequence,
  status.revoked,(long)atomic_get(&auto_stage),(long)atomic_get(&auto_lock),(long long)k_uptime_get(),(long long)end);
 printk("AUTO_STAGE_MS resource=%ld/%ld adapter=%ld/%ld health=%ld/%ld last_lock=%ld/%ld\n",
  (long)atomic_get(&auto_stage_at[0]),(long)atomic_get(&auto_stage_at[1]),
  (long)atomic_get(&auto_stage_at[2]),(long)atomic_get(&auto_stage_at[3]),
  (long)atomic_get(&auto_stage_at[4]),(long)atomic_get(&auto_stage_at[5]),
  (long)atomic_get(&auto_lock_at[0]),(long)atomic_get(&auto_lock_at[1]));
 assert(!"worker failed to complete");
}
static struct drive_completion await_result(enum drive_task mode,uint64_t revision,uint64_t sequence)
{
 struct drive_completion result;
 await_result_into(mode,revision,sequence,&result);return result;
}
K_THREAD_STACK_DEFINE(join_stack,2048);
static struct k_thread join_thread;
K_SEM_DEFINE(join_entered,0,1);
static int join_rc;
static int64_t join_deadline;
static void join_auto(void *a,void *b,void *c)
{
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);k_sem_give(&join_entered);
 join_rc=drive_task_join(DRIVE_TASK_AUTO,1,join_deadline);
}
static void local_lifetime(const char *scenario)
{
 bool residual=!strcmp(scenario,"local-residual");
 fail_restart=!strcmp(scenario,"restart-fail");
 if(!fail_restart){original=automatic.calculate;automatic.calculate=held;}
 assert(!drive_workers_start(&automatic,&manual));
 prepare(1,1);assert(!drive_workers_offer(DRIVE_TASK_AUTO,&job));
 if(!fail_restart)assert(!k_sem_take(&entered,K_MSEC(100)));
 else (void)await_result(DRIVE_TASK_AUTO,1,1);
 int64_t end=k_uptime_get()+(residual?10:250);
 assert(!drive_task_cancel(DRIVE_TASK_AUTO,1,end));
 struct drive_completion old;
 assert(drive_workers_read(DRIVE_TASK_AUTO,1,&old)==-ESHUTDOWN);
 assert(drive_workers_offer(DRIVE_TASK_AUTO,&job)==-ESHUTDOWN);
 struct auto_cfg fresh_cfg;struct drive_adapter fresh;drive_auto_adapter(&fresh,&fresh_cfg);
 if(!fail_restart)assert(drive_task_restart(DRIVE_TASK_AUTO,1,&fresh)==-EBUSY);
 if(residual){
  assert(drive_task_join(DRIVE_TASK_AUTO,1,end)==-ETIMEDOUT);
  assert(drive_task_restart(DRIVE_TASK_AUTO,1,&fresh)==-EBUSY);
  int64_t before=k_uptime_get();
  assert(drive_task_join(DRIVE_TASK_AUTO,1,before+1000)==-ETIMEDOUT);
  assert(k_uptime_get()-before<50);
 }else if(!fail_restart){
  join_deadline=end;
  k_thread_create(&join_thread,join_stack,K_THREAD_STACK_SIZEOF(join_stack),join_auto,NULL,NULL,NULL,8,0,K_NO_WAIT);
  assert(!k_sem_take(&join_entered,K_MSEC(100)));
  struct drive_task_status status;
  do{assert(!drive_workers_status(DRIVE_TASK_AUTO,&status));if(!status.joining)k_sleep(K_MSEC(1));}while(!status.joining);
  assert(drive_task_join(DRIVE_TASK_AUTO,1,end)==-EBUSY);
 }
 for(uint64_t i=1;i<=5;i++){
  prepare(i,1);assert(!drive_workers_offer(DRIVE_TASK_MANUAL,&job));
  struct drive_completion progress=await_result(DRIVE_TASK_MANUAL,1,i);
  assert(progress.progress==i&&progress.source.lifetime==1);
 }
 if(!fail_restart)k_sem_give(&release);
 if(!residual&&!fail_restart){assert(!k_thread_join(&join_thread,K_MSEC(300)));assert(!join_rc);}
 else{if(residual)k_sleep(K_MSEC(5));assert(!drive_task_join(DRIVE_TASK_AUTO,1,k_uptime_get()+100));}
 unsigned before=creates;
 int expected=fail_restart?-ENOMEM:0;
 assert(drive_task_restart(DRIVE_TASK_AUTO,1,&fresh)==expected);
 assert(creates==before+1);
 assert(drive_task_restart(DRIVE_TASK_AUTO,1,&fresh)==expected&&creates==before+1);
 assert(drive_task_cancel(DRIVE_TASK_AUTO,1,k_uptime_get()+100)==-EXDEV);
 struct drive_task_status a,b;assert(!drive_workers_status(DRIVE_TASK_AUTO,&a));
 assert(!drive_workers_status(DRIVE_TASK_MANUAL,&b)&&b.lifetime==1&&!b.revoked);
 assert(a.lifetime==2&&a.revoked==fail_restart);
 if(fail_restart){
  /* A distinct explicitly admitted lifetime may retry a failed creation;
   * replay of the original lifetime above never retries it implicitly. */
  assert(!drive_task_cancel(DRIVE_TASK_AUTO,2,k_uptime_get()+100));
  assert(!drive_task_join_poll(DRIVE_TASK_AUTO,2));
  assert(!drive_task_restart(DRIVE_TASK_AUTO,2,&fresh)&&creates==before+2);
  prepare(1,1);job.lifetime=3;assert(!drive_workers_offer(DRIVE_TASK_AUTO,&job));
  assert(await_result(DRIVE_TASK_AUTO,1,1).source.lifetime==3);
 }
 if(!fail_restart){
  prepare(1,1);job.lifetime=1;assert(drive_workers_offer(DRIVE_TASK_AUTO,&job)==-EXDEV);
  job.lifetime=0;assert(drive_workers_offer(DRIVE_TASK_AUTO,&job)==-EXDEV);
  job.lifetime=2;assert(!drive_workers_offer(DRIVE_TASK_AUTO,&job));
  struct drive_completion progress=await_result(DRIVE_TASK_AUTO,1,1);
  assert(progress.progress==1&&progress.source.lifetime==2);
 }
 assert(!drive_workers_join(k_uptime_get()+300));
 puts("PASS local revoke/join/restart: MANUAL progresses during AUTO cleanup; stale lifetime rejected; retry creates zero extra threads");
 exit(0);
}
static drive_calculate manual_original;
static atomic_t hold_manual;
static unsigned manual_calls;
K_SEM_DEFINE(manual_entered,0,1);
K_SEM_DEFINE(manual_release,0,1);
static int held_manual(void *context,struct xm_span control,struct xm_span input,
                       int64_t age,struct drive_sample *out)
{
 ++manual_calls;
 if(atomic_cas(&hold_manual,1,0)){
  k_sem_give(&manual_entered);k_sem_take(&manual_release,K_FOREVER);
 }
 return manual_original(context,control,input,age,out);
}
static struct xdr_app_region memory_regions[3];
static struct xdr_app_buffer memory_detail[3];
static void memory_read(void)
{
 int64_t end=k_uptime_get()+100;int rc;
 do {
  rc=drive_workers_memory_read(memory_regions,memory_detail);
  assert(!rc||rc==-EBUSY);
  if(rc)k_sleep(K_MSEC(1));
 }while(rc&&k_uptime_get()<end);
 assert(!rc);
}
static void manual_memory(void)
{
 /* One test-owned copy; ARM must not stack multiple 2592-byte return temporaries. */
 static struct drive_completion finished;
 assert(drive_workers_memory_read(memory_regions,memory_detail)==-ENODEV);
 manual_original=manual.calculate;manual.calculate=held_manual;
 atomic_set(&hold_manual,1);
 assert(!drive_workers_start(&automatic,&manual));
 memory_read();
 for(unsigned i=0;i<3;i++){
  size_t bytes=i==2?sizeof(struct drive_completion):sizeof(struct drive_job);
  assert(memory_regions[i].reserved==bytes&&memory_regions[i].capacity==bytes);
  assert(!memory_regions[i].used&&!memory_regions[i].peak);
  assert(memory_detail[i].phase==XDR_BUFFER_EMPTY&&memory_detail[i].source_ms==-1);
 }
 prepare(1,1);job.input_received_ms=-1;
 assert(!drive_workers_offer(DRIVE_TASK_MANUAL,&job));
 assert(!k_sem_take(&manual_entered,K_MSEC(100)));
 memory_read();
 assert(!memory_regions[0].used&&memory_regions[1].used==sizeof(struct drive_job));
 assert(memory_detail[1].sequence==1&&memory_detail[1].source_ms==-1);
 assert(memory_detail[1].phase==XDR_BUFFER_RUNNING&&memory_detail[1].performed);
 uint64_t immutable_content=memory_detail[1].content_bytes;
 prepare(2,1);assert(!drive_workers_offer(DRIVE_TASK_MANUAL,&job));
 prepare(3,1);strcpy(job.input,"{\"keys\":[\"left\"]}");job.input_length=strlen(job.input);
 size_t latest_content=job.control_length+job.input_length;
 assert(!drive_workers_offer(DRIVE_TASK_MANUAL,&job));
 memset(job.input,0,sizeof(job.input));
 memory_read();
 assert(memory_regions[0].used==sizeof(struct drive_job));
 assert(memory_regions[1].used==sizeof(struct drive_job));
 assert(memory_detail[0].sequence==3&&memory_detail[0].content_bytes==latest_content);
 assert(memory_detail[1].sequence==1&&memory_detail[1].content_bytes==immutable_content);
 struct drive_task_status status;assert(!drive_workers_status(DRIVE_TASK_MANUAL,&status)&&status.replaced==1);
 /* A held MANUAL callback cannot stop the actual AUTO owner. */
 prepare(1,1);assert(!drive_workers_offer(DRIVE_TASK_AUTO,&job));
 await_result_into(DRIVE_TASK_AUTO,1,1,&finished);assert(finished.progress==1);
 memory_observer=k_current_get();uint64_t contention=memory_regions[0].contention;
 memory_busy_next=true;
 assert(drive_workers_memory_read(memory_regions,memory_detail)==-EBUSY);
 memory_read();assert(memory_regions[0].contention==contention);
 memory_busy_next=true;
 assert(drive_workers_offer(DRIVE_TASK_MANUAL,&job)==-EBUSY);
 memory_read();assert(memory_regions[0].contention==contention+1);
 k_sem_give(&manual_release);
 await_result_into(DRIVE_TASK_MANUAL,1,3,&finished);
 assert(finished.performed&&!finished.rc&&manual_calls==2);
 assert(!strcmp(finished.source.input,"{\"keys\":[\"left\"]}"));
 memory_read();
 assert(!memory_regions[0].used&&!memory_regions[1].used);
 assert(memory_regions[2].used==sizeof(struct drive_completion));
 assert(memory_detail[2].phase==XDR_BUFFER_RETAINED&&memory_detail[2].sequence==3);
 assert(memory_detail[2].content_bytes==sizeof(struct drive_completion)&&memory_detail[2].read_sequence==3);
 uint64_t read_contention=memory_regions[0].contention;
 memory_busy_next=true;
 assert(drive_workers_read(DRIVE_TASK_MANUAL,1,&finished)==-EBUSY);
 memory_read();assert(memory_regions[0].contention==read_contention+1);
 assert(!drive_workers_read(DRIVE_TASK_MANUAL,1,&finished));
 memory_read();assert(memory_regions[2].used==sizeof(struct drive_completion)&&memory_detail[2].read_sequence==3);
 prepare(4,1);job.release_us=0;job.deadline_us=1;
 assert(!drive_workers_offer(DRIVE_TASK_MANUAL,&job));
 await_result_into(DRIVE_TASK_MANUAL,1,4,&finished);
 assert(!finished.performed&&finished.rc==-ETIMEDOUT);
 memory_read();
 assert(memory_detail[2].source_rc==-ETIMEDOUT&&!memory_detail[2].performed);
 assert(memory_detail[2].input_status==XDR_INPUT_EXPIRED);
 atomic_set(&hold_manual,1);prepare(5,1);
 assert(!drive_workers_offer(DRIVE_TASK_MANUAL,&job));
 assert(!k_sem_take(&manual_entered,K_MSEC(100)));
 prepare(6,1);assert(!drive_workers_offer(DRIVE_TASK_MANUAL,&job));
 int64_t end=k_uptime_get()+1;
 assert(!drive_task_cancel(DRIVE_TASK_MANUAL,1,end));
 assert(drive_task_join(DRIVE_TASK_MANUAL,1,end)==-ETIMEDOUT);
 memory_read();
 assert(memory_regions[1].used==sizeof(struct drive_job)&&memory_detail[1].sequence==5);
 assert(memory_detail[1].revoked&&memory_detail[1].phase==XDR_BUFFER_RUNNING);
 assert(memory_regions[2].used==sizeof(struct drive_completion)&&memory_detail[2].sequence==4);
 prepare(2,1);assert(!drive_workers_offer(DRIVE_TASK_AUTO,&job));
 await_result_into(DRIVE_TASK_AUTO,1,2,&finished);assert(finished.progress==2);
 k_sem_give(&manual_release);
 end=k_uptime_get()+300;int joined;
 do {
  joined=drive_task_join_poll(DRIVE_TASK_MANUAL,1);
  assert(!joined||joined==-EAGAIN||joined==-ETIMEDOUT);
  if(joined)k_sleep(K_MSEC(1));
 }while(joined&&k_uptime_get()<end);
 assert(!joined);
 memory_read();
 assert(!memory_regions[0].used&&!memory_regions[1].used&&memory_regions[2].used);
 struct manual_cfg fresh_cfg;struct drive_adapter fresh;drive_manual_adapter(&fresh,&fresh_cfg);
 assert(!drive_task_restart(DRIVE_TASK_MANUAL,1,&fresh));
 memory_read();
 for(unsigned i=0;i<3;i++){
  size_t bytes=i==2?sizeof(struct drive_completion):sizeof(struct drive_job);
  assert(!memory_regions[i].used&&memory_regions[i].peak==bytes);
  assert(memory_detail[i].phase==XDR_BUFFER_EMPTY&&!memory_detail[i].read_sequence);
  assert(memory_regions[i].contention>=1);
 }
 prepare(1,1);job.lifetime=2;
 assert(!drive_workers_offer(DRIVE_TASK_MANUAL,&job));
 await_result_into(DRIVE_TASK_MANUAL,1,1,&finished);assert(finished.source.lifetime==2);
 assert(!drive_workers_join(k_uptime_get()+300));
 memory_read();
 assert(!memory_regions[0].used&&!memory_regions[1].used&&memory_regions[2].used);
 assert(memory_detail[2].revoked);
 printf("PASS MANUAL memory: pending/running/result=%zu/%zu/%zu; immutable/replaced, retained/read, expiry, residual, restart peak and AUTO progress\n",
        sizeof(struct drive_job),sizeof(struct drive_job),sizeof(struct drive_completion));
 printf("MEMORY_ABI snapshot=%zu detail=%zu region=%zu\n",sizeof(struct xdr_snapshot),sizeof(struct xdr_app_buffer),sizeof(struct xdr_app_region));
 exit(0);
}
int main(void)
{
#if defined(CONFIG_ARCH_POSIX)
 const char *scenario=getenv("XL_CASE");
#else
 const char *scenario="memory-manual";
#endif
 assert(scenario);
 creates=0; /* Exclude native kernel service threads created before main. */
 drive_auto_adapter(&automatic,&auto_cfg);drive_manual_adapter(&manual,&manual_cfg);
	if(!strcmp(scenario,"auto-delay-policy")){
		struct drive_auto_delay_status status;
		assert(drive_auto_delay_start(0,1)==-EINVAL);
		assert(drive_auto_delay_start(DRIVE_AUTO_DELAY_MAX_MS+1,1)==-EINVAL);
		assert(!drive_auto_delay_start(2,1));
		assert(drive_auto_delay_start(2,1)==-EBUSY);
		int64_t before=k_uptime_get();drive_auto_delay_apply();
		assert(k_uptime_get()>=before+2);
		for(unsigned i=1;i<DRIVE_AUTO_DEADLINE_MISS_LIMIT;i++)
			assert(!drive_auto_delay_observe_deadline(true));
		assert(drive_auto_delay_observe_deadline(true));
		drive_auto_delay_status_get(&status);
		assert(status.active&&status.unhealthy&&
		 status.consecutive_deadline_misses==DRIVE_AUTO_DEADLINE_MISS_LIMIT);
		assert(!drive_auto_delay_observe_deadline(false));
		drive_auto_delay_status_get(&status);
		assert(!status.unhealthy&&!status.consecutive_deadline_misses);
		drive_auto_delay_stop();assert(!drive_auto_delay_active());
		puts("PASS AUTO delay: real busy wait, five consecutive misses, normal completion resets");
		exit(0);
	}
 if(!strcmp(scenario,"memory-manual"))manual_memory();
 if(!strncmp(scenario,"local-",6)||!strcmp(scenario,"restart-fail"))local_lifetime(scenario);
 if(!strcmp(scenario,"not-started")){
  assert(!drive_workers_join(k_uptime_get()+10));
  assert(drive_workers_start(&automatic,&manual)==-EALREADY);
  puts("PASS no wait for unstarted owners");exit(0);
 }
 if(!strcmp(scenario,"partial")){
  partial=true;assert(drive_workers_start(&automatic,&manual)==-ENOMEM);
  assert(!drive_workers_join(k_uptime_get()+100));
  struct drive_task_status a,b;
  assert(!drive_workers_status(DRIVE_TASK_AUTO,&a)&&a.started&&a.joined);
  assert(!drive_workers_status(DRIVE_TASK_MANUAL,&b)&&!b.started&&!b.joined);
  puts("PASS partial start joins only admitted thread");exit(0);
 }
 bool stall=!strcmp(scenario,"independent")||!strcmp(scenario,"coalesce")||!strcmp(scenario,"residual");
 if(stall){original=automatic.calculate;automatic.calculate=held;}
 assert(!drive_workers_start(&automatic,&manual));
 assert(drive_workers_start(&automatic,&manual)==-EALREADY);
 prepare(1,1);
 if(stall){assert(!drive_workers_offer(DRIVE_TASK_AUTO,&job));assert(!k_sem_take(&entered,K_MSEC(100)));}
 if(!strcmp(scenario,"expired")){
  /* Native time can still be zero at the first release; avoid unsigned
   * underflow in the deliberately expired test deadline. */
  k_sleep(K_MSEC(2));prepare(1,1);
  job.deadline_us=job.release_us-1;job.release_us=0;
  assert(!drive_workers_offer(DRIVE_TASK_MANUAL,&job));
  struct drive_completion r=await_result(DRIVE_TASK_MANUAL,1,1);
  assert(!r.progress&&r.rc==-ETIMEDOUT&&r.health.restrict_function);
 }else if(!strcmp(scenario,"coalesce")){
  prepare(2,1);assert(!drive_workers_offer(DRIVE_TASK_AUTO,&job));
  prepare(3,1);assert(!drive_workers_offer(DRIVE_TASK_AUTO,&job));
  k_sem_give(&release);
  struct drive_completion r=await_result(DRIVE_TASK_AUTO,1,3);
  struct drive_task_status s;assert(!drive_workers_status(DRIVE_TASK_AUTO,&s));
  assert(r.progress==2&&auto_calls==2&&s.replaced==1);
 }else{
  assert(!drive_workers_offer(DRIVE_TASK_MANUAL,&job));
  assert(drive_workers_offer(DRIVE_TASK_MANUAL,&job)==-EALREADY);
  memset(job.input,0,sizeof(job.input));
  struct drive_completion r=await_result(DRIVE_TASK_MANUAL,1,1);
  assert(r.progress==1&&r.sample.v_mps>0&&!r.health.restrict_function);
  assert(drive_workers_read(DRIVE_TASK_MANUAL,2,&r)==-EXDEV);
 }
 if(!strcmp(scenario,"residual")){
  int64_t end=k_uptime_get()+20;
  assert(drive_workers_join(end)==-ETIMEDOUT);
  struct drive_task_status s;assert(!drive_workers_status(DRIVE_TASK_AUTO,&s));
  assert(s.state==DRIVE_TASK_RESIDUAL&&!s.joined);
  prepare(2,2);assert(drive_workers_offer(DRIVE_TASK_MANUAL,&job)==-ESHUTDOWN);
  int64_t before=k_uptime_get();
  assert(drive_workers_join(before+1000)==-ETIMEDOUT);
  assert(k_uptime_get()-before<50); /* original budget, not new allowance */
  k_sem_give(&release);k_sleep(K_MSEC(10));
  assert(!drive_workers_join(k_uptime_get()+1000));
 }else{
  if(stall)k_sem_give(&release);
  assert(!drive_workers_join(k_uptime_get()+300));
 }
 assert(!drive_workers_join(k_uptime_get()+300));
 puts("PASS real adapters: independent progress, immutable input, context, bounded ownership");
 exit(0);
}
