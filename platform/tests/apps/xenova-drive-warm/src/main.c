/* Real main and calculation threads. Incoming/outgoing transport is external;
 * controlled AUTO delay does not replace either existing calculation. */
#define main product_main
#include XL_MAIN_SOURCE
#undef main
#include <assert.h>
#include <stdlib.h>
#include <zephyr/sys/atomic.h>
#include <xenova/drive_context.h>

#define AUTO_ID "11111111-1111-4111-8111-111111111111"
#define NEW_ID "22222222-2222-4222-8222-222222222222"
#define MANUAL_ID "33333333-3333-4333-8333-333333333333"
static unsigned calls[2],reports[2],commands,manual_while_auto,rotated_reports;
static atomic_t auto_held;
static const char *scenario;
static int64_t origin,activation,first_ready;
static bool reset_case,waiting_case,residual_case;
static atomic_t loader_stage,loader_calls;
K_SEM_DEFINE(loader_release,0,1);
static int context_loader(struct xt_bootstrap *out,int64_t end,void *channel)
{
 assert(end>k_uptime_get()&&!strcmp(channel,"xenova-drive"));atomic_inc(&loader_calls);
 int stage=atomic_get(&loader_stage);
 if(stage==3)k_sem_take(&loader_release,K_FOREVER); /* external Store boundary stalls */
 memset(out,0,sizeof(*out));strcpy(out->drive_profile,"warm-drive-1");
 strcpy(out->identity.vm_id,"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
 strcpy(out->identity.boot_id,MANUAL_ID);strcpy(out->identity.publisher_id,AUTO_ID);
 strcpy(out->identity.execution_id,stage==2?NEW_ID:MANUAL_ID);
 strcpy(out->auto_instance_id,stage==1?NEW_ID:AUTO_ID);strcpy(out->manual_instance_id,MANUAL_ID);
 return 0;
}
static void context_test(void)
{
 struct xt_bootstrap value;
 assert(!xd_context_start(context_loader));
 for(unsigned i=0;i<100&&xd_context_get(&value);i++)k_sleep(K_MSEC(2));
 assert(!xd_context_get(&value)&&!strcmp(value.auto_instance_id,AUTO_ID));
 atomic_set(&loader_stage,1);k_sleep(K_MSEC(120));
 assert(!xd_context_get(&value)&&!strcmp(value.auto_instance_id,NEW_ID)&&!strcmp(value.manual_instance_id,MANUAL_ID));
 atomic_set(&loader_stage,2);k_sleep(K_MSEC(120));assert(xd_context_get(&value)==-EAGAIN);
 atomic_set(&loader_stage,3);k_sleep(K_MSEC(120));
 int64_t begin=k_uptime_get();assert(xd_context_get(&value)==-EAGAIN&&k_uptime_get()-begin<10);
 assert(xd_context_join(k_uptime_get()+10)==-ETIMEDOUT);
 k_sem_give(&loader_release);assert(!xd_context_join(k_uptime_get()+100));
 assert(xd_context_get(&value)==-EAGAIN);
 printf("PASS root context: per-app refresh, base replacement refused, nonblocking reads, join residual and late termination\n");
}

int __real_drive_adapter_run_observed(struct drive_adapter *,struct xm_span,struct xm_span,int64_t,const struct xd_manual_witness *,const char *,struct drive_sample *);
int __wrap_drive_adapter_run_observed(struct drive_adapter *a,struct xm_span control,struct xm_span input,int64_t age,
 const struct xd_manual_witness *w,const char *receiver,struct drive_sample *out)
{
 bool automatic=a->context==&controller.auto_cfg;
 if(automatic&&!calls[0]&&reset_case){atomic_set(&auto_held,1);k_sleep(K_MSEC(residual_case?650:120));atomic_set(&auto_held,0);}
 if(!automatic&&atomic_get(&auto_held))manual_while_auto++;
 calls[automatic?0:1]++;
 return __real_drive_adapter_run_observed(a,control,input,age,w,receiver,out);
}
int __wrap_xd_io_start_dual(struct xm_zclient *c,const char *control,const char *input,const char *perception)
{ARG_UNUSED(c);ARG_UNUSED(control);ARG_UNUSED(input);ARG_UNUSED(perception);return 0;}
int __wrap_xd_io_snapshot(struct xd_inputs *out)
{
 memset(out,0,sizeof(*out));int64_t now=k_uptime_get(),elapsed=now-origin;
 bool active=!waiting_case&&(reset_case||elapsed>=120);
 bool automatic=reset_case&&elapsed<35;
 uint32_t generation=!active?1:automatic?2:3;
 out->control_revision=generation;out->connection_revision=1;out->connected=true;out->dual_mode=true;
 strcpy(out->vm_id,"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
 strcpy(out->app_instances[0],reset_case&&elapsed>=45?NEW_ID:AUTO_ID);strcpy(out->app_instances[1],MANUAL_ID);
 snprintf(out->control.payload,sizeof(out->control.payload),
  "{\"generation\":%u,\"mode\":\"%s\",\"active_app\":%s,\"active_vm_id\":%s}",
  generation,!active?"SAFE_STOP":automatic?"AUTO":"MANUAL",
  !active?"null":automatic?"\"autonomous\"":"\"manual\"",!active?"null":"\"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa\"");
 strcpy(out->input.payload,"{\"keys\":[\"up\"]}");
 strcpy(out->perception.payload,"{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[]}");
 struct xd_received *rows[]={&out->control,&out->input,&out->perception};
 for(size_t i=0;i<ARRAY_SIZE(rows);i++){rows[i]->length=strlen(rows[i]->payload);rows[i]->received_ms=now;rows[i]->seq=now+1;}
 if(active&&!automatic&&!activation)activation=now;
 return 0;
}
int __wrap_xd_io_report_at(const char *topic,const char *type,const char *json,uint64_t sequence,const struct xd_inputs *context,int64_t produced)
{
 assert(sequence&&produced<=k_uptime_get());bool automatic=strstr(topic,"/autonomous/")!=NULL;
 if(!strcmp(type,"app.lifecycle")){
  reports[automatic?0:1]++;assert(strstr(json,"\"profile\":\"warm-drive-1\""));
  if(waiting_case){assert(strstr(json,"\"phase\":\"WAITING\"")&&strstr(json,"\"progress_seq\":\"0\""));}
  if(!automatic&&strstr(json,"\"readiness\":\"READY\"")&&!first_ready)first_ready=k_uptime_get();
  if(automatic&&!strcmp(lifecycle[0].instance,NEW_ID)){
   struct drive_task_status s;assert(!drive_workers_status(0,&s)&&s.lifetime==2&&!s.revoked);
   assert(!atomic_get(&auto_held)&&!strcmp(context->app_instances[0],NEW_ID));rotated_reports++;
  }
 }
 return 0;
}
int __wrap_xd_io_command(struct xd_candidate *c)
{assert(!strcmp(c->app_instance,MANUAL_ID)||!strcmp(c->app_instance,AUTO_ID));commands++;return 0;}
int __wrap_xd_io_join(int64_t end){assert(end>0);return 0;}
int __wrap_xt_start(xt_bootstrap_loader loader,void *context,const struct xt_inventory *inventory)
{ARG_UNUSED(loader);ARG_UNUSED(context);assert(inventory);return 0;}
int __wrap_xt_stop(int64_t end){assert(end>0);return 0;}
int __wrap_xt_status_get(struct xt_status *out){memset(out,0,sizeof(*out));return 0;}
void __real_exit(int);
void __wrap_exit(int rc)
{
 assert(!rc&&reports[0]&&reports[1]);
 if(waiting_case)assert(!calls[0]&&!calls[1]&&!commands);
 else{
  assert(calls[1]>5&&commands>5&&first_ready&&first_ready-activation<80);
  if(reset_case){assert(calls[0]==1&&manual_while_auto>=3&&rotated_reports);
   assert(!strcmp(lifecycle[0].instance,NEW_ID)&&!strcmp(lifecycle[1].instance,MANUAL_ID));}
  else assert(!calls[0]);
 }
 for(unsigned i=0;i<2;i++){struct drive_task_status s;assert(!drive_workers_status(i,&s)&&s.joined);}
 printf("PASS warm product main %s: AUTO=%u MANUAL=%u reports=%u/%u commands=%u old lifetime rejected; joined\n",
  scenario,calls[0],calls[1],reports[0],reports[1],commands);__real_exit(0);
}
int main(void)
{
 scenario=getenv("XL_CASE");assert(scenario);
 if(!strcmp(scenario,"context")){context_test();__real_exit(0);return 0;}
 waiting_case=!strcmp(scenario,"waiting");residual_case=!strcmp(scenario,"residual");
 reset_case=residual_case||!strcmp(scenario,"reset");origin=k_uptime_get();
 setenv("XD_TEST_DURATION_MS",residual_case?"850":"400",1);return product_main();
}
