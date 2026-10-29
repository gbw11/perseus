/* SPDX-License-Identifier: Apache-2.0 */
/* A/B share this binary and one v1 I/O owner. Each calculation has its own
 * preemptible owner; the coordinator never waits for a calculation callback. */
#include <zephyr/kernel.h>
#include <xenova/drive_runtime.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
#include "controller.h"
#include "workers.h"

static struct xm_zclient client;
static struct drive_controller controller;
static struct xd_inputs inputs, source;
static struct drive_job job;
static struct drive_completion completed[DRIVE_TASK_COUNT];
static struct xdh_health health[DRIVE_TASK_COUNT];
static uint64_t seen[DRIVE_TASK_COUNT];
static uint64_t assessed_revision[DRIVE_TASK_COUNT], assessed_connection[DRIVE_TASK_COUNT];
static int64_t next_beat[DRIVE_TASK_COUNT], next_readiness[DRIVE_TASK_COUNT];
static void announce(const char *events,enum auto_reason previous,const struct drive_result *result,
 const struct drive_completion *work)
{
 const char *kind=NULL;char detail[48],json[96];
 if(result->auto_reason==AUTO_REASON_OBSTACLE_STOP){
  kind="obstacle";
  if(result->nearest_m>=0)snprintf(detail,sizeof(detail),"stop %d cm",(int)(result->nearest_m*100+.5f));
  else strcpy(detail,"stop");
 }else if(result->auto_reason==AUTO_REASON_NO_PERCEPTION){kind="perception_lost";strcpy(detail,"stop");}
 else if(previous==AUTO_REASON_OBSTACLE_STOP){kind="obstacle_cleared";strcpy(detail,"resume");}
 else if(previous==AUTO_REASON_NO_PERCEPTION){kind="perception_restored";strcpy(detail,"resume");}
 if(kind&&xm_perception_event_json(json,sizeof(json),kind,detail)>0)
  (void)xd_publish_completed(events,"perception.event",json,work->sequence,work->finished_ms,&source);
}
static int finish(int result)
{
 drive_workers_stop();
 int cleanup=drive_workers_join(xd_cleanup_deadline_ms());
 if(cleanup)xd_app_cleanup_error(cleanup);
 return xd_finish(&client,result?result:cleanup);
}
static void provenance(const struct drive_completion *work,enum drive_task task)
{
 memset(&source,0,sizeof(source));
 strcpy(source.vm_id,work->source.vm_id);source.dual_mode=true;
 source.control_revision=work->source.context_revision;
 source.connection_revision=work->source.connection_revision;
 source.control.seq=work->source.control_seq;
 source.input.error=source.perception.error=-ENODATA;
 struct xd_received *in=task==DRIVE_TASK_AUTO?&source.perception:&source.input;
 in->seq=work->source.input_seq;in->received_ms=work->source.input_received_ms;
 in->error=work->source.input_length?0:-ENODATA;
}
static int offer(enum drive_task task,uint64_t release,uint64_t sequence,uint64_t iteration_begin)
{
 const struct xd_received *in=task==DRIVE_TASK_AUTO?&inputs.perception:&inputs.input;
 memset(&job,0,sizeof(job));
 job.sequence=sequence;job.context_revision=inputs.control_revision;
 job.connection_revision=inputs.connection_revision;
 job.release_us=release;job.deadline_us=release+20000;
 job.iteration_begin_us=iteration_begin;
 job.input_seq=in->seq;job.control_seq=inputs.control.seq;job.input_received_ms=in->received_ms;
 strcpy(job.vm_id,inputs.vm_id);
 if(!inputs.control.error)job.control_length=inputs.control.length;
 if(!in->error)job.input_length=in->length;
 if(job.control_length>sizeof(job.control)-1||job.input_length>sizeof(job.input)-1)return -EMSGSIZE;
 memcpy(job.control,inputs.control.payload,job.control_length);
 memcpy(job.input,in->payload,job.input_length);
 return drive_workers_offer(task,&job);
}
int main(void)
{
 char control[XM_TOPIC_MAX],input[XM_TOPIC_MAX],perception[XM_TOPIC_MAX];
 char events[XM_TOPIC_MAX],json[160];
 xm_topic_vehicle_control(control,sizeof(control));xm_topic_manual_input(input,sizeof(input));
 xm_topic_app_events(perception,sizeof(perception),"perception");
 drive_controller_init(&controller);
 int rc=xd_start("drive-controller",20);
 if(rc)return finish(rc);
 rc=drive_workers_start(&controller.automatic,&controller.manual);
 if(rc)return finish(rc);
 uint64_t origin=xd_next_release_us(),next=origin;
 int64_t start=k_uptime_get();
 enum auto_reason last_reason=AUTO_REASON_NO_PERCEPTION;bool seen_perception=false;
 while(!xd_stopping()){
  int io=xd_client_prepare_dual(&client,control,input,perception);
  if(io&&io!=-EAGAIN&&io!=-EBUSY){rc=io;break;}
  if((rc=xd_iteration_begin()))break;
  xd_inputs_copy(&inputs);
  struct xm_span cp={inputs.control.payload,inputs.control.length};
  if(inputs.control.error)cp=(struct xm_span){0};
  struct drive_result selected;drive_controller_select(&controller,inputs.vm_id,cp,&selected);
  uint64_t now;if((rc=xdiag_clock_us(&now)))break;
  if(now>=next){
   uint64_t release=next+((now-next)/20000)*20000;
   if(release>UINT64_MAX-20000){rc=-EOVERFLOW;break;}
   next=release+20000;
   if(selected.control_valid&&inputs.control_revision&&strlen(inputs.vm_id)==XM_UUID_LEN){
    for(unsigned task=0;task<DRIVE_TASK_COUNT;task++){
     int status=offer(task,release,(release-origin)/20000+1,now);
     if(status&&status!=-EBUSY&&status!=-EALREADY){rc=status;break;}
    }
    if(rc)break;
   }
  }
  for(unsigned task=0;task<DRIVE_TASK_COUNT;task++){
   struct drive_completion *work=&completed[task];
   int status=drive_workers_read(task,inputs.control_revision,work);
   if(status==-ENODATA||status==-EXDEV||status==-EBUSY)continue;
   if(status){rc=status;break;}
   if(work->sequence<=seen[task])continue;
   seen[task]=work->sequence;
   if(!work->performed||!selected.control_valid||
      work->source.connection_revision!=inputs.connection_revision||strcmp(work->source.vm_id,inputs.vm_id))continue;
   health[task]=work->health;
   assessed_revision[task]=work->context_revision;
   assessed_connection[task]=work->source.connection_revision;
   provenance(work,task);
   bool automatic=task==DRIVE_TASK_AUTO;
   bool chosen=automatic?selected.mode==DRIVE_AUTO:selected.mode!=DRIVE_AUTO;
   xm_topic_vm_app_events(events,sizeof(events),inputs.vm_id,automatic?AUTO_APP_ID:MANUAL_APP_ID);
   int64_t tick=k_uptime_get();
   if(tick>=next_readiness[task]&&!xd_stopping()){
    const char *reason=work->sample.reason;
    const char *ready=!reason?"READY":!strcmp(reason,"STATE_PREPARING")?"PREPARING":"NOT_READY";
    int n=xm_app_readiness_json(json,sizeof(json),selected.generation,work->progress,ready,reason);
    if(n>0)(void)xd_publish_completed(events,"app.readiness",json,work->sequence,work->finished_ms,&source);
    next_readiness[task]=tick+100;
   }
   if(tick>=next_beat[task]&&!xd_stopping()){
    const char *state=work->health.restrict_function?"error":selected.locally_active&&chosen?"active":"standby";
    int n=xm_app_heartbeat_json(json,sizeof(json),state,tick-start);
    if(n>0)(void)xd_publish_completed(events,"app.heartbeat",json,work->sequence,work->finished_ms,&source);
    next_beat[task]=tick+1000;
   }
   if(!chosen)continue;
   status=xd_completed_work(work->source.release_us,work->begin_us,work->end_us,
    work->source.iteration_begin_us,work->finished_ms,&source);
   if(status==-EALREADY)continue;
   if(status){rc=status;break;}
   /* Never publish an unassessed mode's zero-initialized health, nor attach
    * a result from an older control/connection to the new pair. */
   if(assessed_revision[0]==inputs.control_revision&&assessed_revision[1]==inputs.control_revision&&
      assessed_connection[0]==inputs.connection_revision&&assessed_connection[1]==inputs.connection_revision)
    xd_health_record(&health[DRIVE_TASK_AUTO],&health[DRIVE_TASK_MANUAL]);
   if(selected.mode!=DRIVE_STOP){
    if((rc=xd_command_input(automatic?"perception.result":"drive.input")))break;
    int encoded=xm_drive_command_json(json,sizeof(json),selected.generation,work->sample.v_mps,work->sample.steer_rad);
    xd_command(&client,events,json,encoded,selected.generation,work->sample.v_mps,work->sample.steer_rad,
     work->source.input_seq,work->source.control_seq,tick-work->source.input_received_ms,
     automatic?controller.auto_cfg.perception_timeout_ms:controller.manual_cfg.input_timeout_ms);
   }
   if(automatic&&work->sample.auto_reason!=last_reason){
    selected.auto_reason=work->sample.auto_reason;selected.nearest_m=work->sample.nearest_m;
    if(selected.locally_active&&seen_perception&&!xd_stopping())announce(events,last_reason,&selected,work);
    seen_perception=seen_perception||selected.auto_reason!=AUTO_REASON_NO_PERCEPTION;
    last_reason=selected.auto_reason;
   }
   xd_iteration_end();
  }
  if(rc)break;
  if(!xd_stopping())k_sleep(K_MSEC(1));
 }
 return finish(rc);
}
