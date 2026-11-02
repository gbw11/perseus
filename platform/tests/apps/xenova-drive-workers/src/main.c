/* Actual kernel owners and production adapters; only callback delay/thread
 * creation failure are injected. No broker, Manager, Xen or mock drive app. */
#include <zephyr/kernel.h>
#include <xenova/diagnostics.h>
#include "workers.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct auto_cfg auto_cfg;
static struct manual_cfg manual_cfg;
static struct drive_adapter automatic, manual;
static struct drive_job job;
static drive_calculate original;
static unsigned auto_calls, creates;
static bool partial, fail_restart;
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
 return __real_z_impl_k_thread_create(thread, stack, size, entry, p1, p2, p3, priority, options, delay);
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
static struct drive_completion await_result(enum drive_task mode, uint64_t revision, uint64_t sequence)
{
 int64_t end=k_uptime_get()+300;
 struct drive_completion result;
 while(k_uptime_get()<end){
  int rc=drive_workers_read(mode,revision,&result);
  if(!rc&&result.sequence==sequence)return result;
  assert(!rc||rc==-ENODATA||rc==-EBUSY);k_sleep(K_MSEC(1));
 }
 assert(!"worker failed to complete");return result;
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
int main(void)
{
 const char *scenario=getenv("XL_CASE");assert(scenario);
 creates=0; /* Exclude native kernel service threads created before main. */
 drive_auto_adapter(&automatic,&auto_cfg);drive_manual_adapter(&manual,&manual_cfg);
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
