/* Actual application main and lifecycle; transport is the only data boundary.
 * AUTO's first real adapter call is delayed, not replaced by a mock algorithm. */
#define main product_main
#include XL_MAIN_SOURCE
#undef main
#include <assert.h>
#include <stdlib.h>
#include <zephyr/sys/atomic.h>
#include <xenova/telemetry.h>

static atomic_t auto_held;
static unsigned calls[2], manual_during_auto, reports[2], commands;
static uint64_t snapshot_revision;
static int64_t fixture_origin;
static bool replace_context;
static int64_t last_finished_ms;
static unsigned health_records;

void __real_xd_health_record(const struct xdh_health *,const struct xdh_health *);
void __wrap_xd_health_record(const struct xdh_health *automatic,const struct xdh_health *manual)
{
 assert(calls[0]&&calls[1]&&!atomic_get(&auto_held));
 for(unsigned i=0;i<DRIVE_TASK_COUNT;i++){
  assert(assessed_revision[i]==snapshot_revision&&assessed_connection[i]==1);
 }
 health_records++;
 __real_xd_health_record(automatic,manual);
}

int __real_xd_completed_work(uint64_t,uint64_t,uint64_t,uint64_t,int64_t,const struct xd_inputs *);
int __wrap_xd_completed_work(uint64_t release,uint64_t begin,uint64_t end,uint64_t iteration,
 int64_t finished,const struct xd_inputs *source)
{
 struct xdiag_snapshot before,after;
 assert(!xdiag_snapshot_get(&before,false)&&!before.timing_rc);
 assert(__real_xd_completed_work(release,begin,end,iteration,k_uptime_get()+1000,source)==-EINVAL);
 assert(!xdiag_snapshot_get(&after,false)&&after.timing.loop_seq==before.timing.loop_seq);
 int rc=__real_xd_completed_work(release,begin,end,iteration,finished,source);
 if(rc)return rc;
 assert(!xdiag_snapshot_get(&after,false)&&after.timing.loop_seq==before.timing.loop_seq+1);
 assert(__real_xd_completed_work(release,begin,end,iteration,finished,source)==-EALREADY);
 assert(!xdiag_snapshot_get(&before,false)&&before.timing.loop_seq==after.timing.loop_seq);
 last_finished_ms=finished;
 return 0;
}

int __real_drive_adapter_run(struct drive_adapter *,struct xm_span,struct xm_span,int64_t,struct drive_sample *);
int __wrap_drive_adapter_run(struct drive_adapter *a,struct xm_span control,struct xm_span input,int64_t age,struct drive_sample *out)
{
 bool automatic=a->context==&controller.auto_cfg;
 if(automatic&&!calls[0]){
  atomic_set(&auto_held,1);k_sleep(K_MSEC(120));atomic_set(&auto_held,0);
 }
 if(!automatic&&atomic_get(&auto_held))manual_during_auto++;
 calls[automatic?0:1]++;
 return __real_drive_adapter_run(a,control,input,age,out);
}
int __wrap_xd_io_start_dual(struct xm_zclient *c,const char *control,const char *input,const char *perception)
{ARG_UNUSED(c);ARG_UNUSED(control);ARG_UNUSED(input);ARG_UNUSED(perception);return 0;}
int __wrap_xd_io_snapshot(struct xd_inputs *out)
{
 memset(out,0,sizeof(*out));
 int64_t now=k_uptime_get();
 snapshot_revision=replace_context&&now-fixture_origin>=60?2:1;
 out->control_revision=snapshot_revision;out->connection_revision=1;
 out->dual_mode=true;out->connected=true;
 strcpy(out->vm_id,"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
 bool automatic=snapshot_revision==2;
 snprintf(out->control.payload,sizeof(out->control.payload),
  "{\"generation\":%u,\"mode\":\"%s\",\"active_app\":\"%s\",\"active_vm_id\":\"%s\"}",
  (unsigned)snapshot_revision,automatic?"AUTO":"MANUAL",automatic?"autonomous":"manual",out->vm_id);
 strcpy(out->input.payload,"{\"keys\":[\"up\"]}");
 strcpy(out->perception.payload,"{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[]}");
 struct xd_received *rows[]={&out->control,&out->input,&out->perception};
 for(size_t i=0;i<ARRAY_SIZE(rows);i++){
  rows[i]->length=strlen(rows[i]->payload);rows[i]->received_ms=now-now%50;rows[i]->seq=now/50+1;
 }
 return 0;
}
int __wrap_xd_io_report_at(const char *topic,const char *type,const char *json,uint64_t completion,const struct xd_inputs *context,int64_t produced)
{
 assert(completion&&produced<=k_uptime_get());
 assert(context->control_revision==snapshot_revision&&context->connection_revision==1);
 bool automatic=strstr(topic,"/autonomous/")!=NULL;
 assert(!automatic||!atomic_get(&auto_held));
 reports[automatic?0:1]++;
 if(!strcmp(type,"app.readiness")){
  char expected[48];snprintf(expected,sizeof(expected),"\"applied_generation\":%u",(unsigned)snapshot_revision);
  assert(strstr(json,expected));
 }
 return 0;
}
int __wrap_xd_io_command(struct xd_candidate *candidate)
{
 assert(candidate->control_revision==snapshot_revision&&candidate->connection_revision==1);
 assert(candidate->created_us<=candidate->queued_us);
 assert(candidate->expires_ms<=last_finished_ms+CONFIG_XENOVA_DRIVE_COMMAND_MAX_AGE_MS);
 commands++;return 0;
}
int __wrap_xd_io_join(int64_t end){assert(end>0);return 0;}
int __wrap_xt_start(xt_bootstrap_loader loader,void *context,const struct xt_inventory *inventory)
{ARG_UNUSED(loader);ARG_UNUSED(context);assert(inventory);return 0;}
int __wrap_xt_stop(int64_t end){assert(end>0);return 0;}
int __wrap_xt_status_get(struct xt_status *out){memset(out,0,sizeof(*out));return 0;}
void __real_exit(int);
void __wrap_exit(int result)
{
 assert(!result&&manual_during_auto>=3&&commands>=5&&calls[0]>=2&&calls[1]>=8);
 assert(reports[0]&&reports[1]&&health_records);
 for(unsigned i=0;i<DRIVE_TASK_COUNT;i++){
  struct drive_task_status status;assert(!drive_workers_status(i,&status)&&status.joined);
 }
 struct xdiag_snapshot diag;assert(!xdiag_snapshot_get(&diag,false));
 assert(!diag.timing_rc&&diag.timing.loop_seq>0);
 printf("PASS product main: MANUAL completed %u times during AUTO delay; %u commands; old context rejected; owners joined\n",
  manual_during_auto,commands);
 __real_exit(0);
}
int main(void)
{
 const char *scenario=getenv("XL_CASE");assert(scenario);
 replace_context=!strcmp(scenario,"context");fixture_origin=k_uptime_get();
 setenv("XD_TEST_DURATION_MS","350",1);
 return product_main();
}
