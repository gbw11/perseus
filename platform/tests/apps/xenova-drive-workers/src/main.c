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
static bool partial;
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
 if (partial && ++creates == 2) return NULL;
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
int main(void)
{
 const char *scenario=getenv("XL_CASE");assert(scenario);
 drive_auto_adapter(&automatic,&auto_cfg);drive_manual_adapter(&manual,&manual_cfg);
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
