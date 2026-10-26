/* SPDX-License-Identifier: Apache-2.0 */
/* A/B share this binary. One runtime/I/O owner, one 20-ms completion and one
 * selected command per cycle; both mode heartbeats follow actual calculation. */
#include <zephyr/kernel.h>
#include <xenova/drive_runtime.h>
#include <errno.h>
#include <string.h>
#include <stdio.h>
#include "controller.h"

static struct xm_zclient client;
static struct drive_controller controller;
static void announce(const char *events,enum auto_reason previous,const struct drive_result *result)
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
  (void)xd_publish(&client,events,"perception.event",json);
}
int main(void)
{
 char control[XM_TOPIC_MAX],input[XM_TOPIC_MAX],perception[XM_TOPIC_MAX];
 char auto_events[XM_TOPIC_MAX],manual_events[XM_TOPIC_MAX],json[160];
 xm_topic_vehicle_control(control,sizeof(control));xm_topic_manual_input(input,sizeof(input));
 xm_topic_app_events(perception,sizeof(perception),"perception");
 xm_topic_app_events(auto_events,sizeof(auto_events),AUTO_APP_ID);
 xm_topic_app_events(manual_events,sizeof(manual_events),MANUAL_APP_ID);
 drive_controller_init(&controller);
 int rc=xd_start("drive-controller",20);
 if(rc)return xd_finish(&client,rc);
 int64_t start=k_uptime_get(),next_beat=start;
 enum auto_reason last_reason=AUTO_REASON_NO_PERCEPTION;bool seen_perception=false;
 while(!xd_stopping()){
  int io=xd_client_prepare_dual(&client,control,input,perception);
  if(io&&io!=-EAGAIN&&io!=-EBUSY){rc=io;break;}
  uint64_t now;
  if((rc=xdiag_clock_us(&now)))break;
  if(now<xd_next_release_us()){k_sleep(K_MSEC(1));continue;}
  if((rc=xd_iteration_begin()))break;
  struct xm_span cp,mp,ap;int64_t ca,ma,aa,cs,ms,as;
  xd_latest(&client,control,"vehicle.control",&cp,&ca,&cs);
  xd_latest(&client,input,"drive.input",&mp,&ma,&ms);
  xd_latest(&client,perception,"perception.result",&ap,&aa,&as);
  struct drive_result result;
  if((rc=xd_step_begin()))break;
  drive_controller_step(&controller,xd_vm_id(),cp,mp,ma,ap,aa,&result);
  if((rc=xd_step_end()))break;
  bool automatic=result.mode==DRIVE_AUTO;
  const char *events=automatic?auto_events:manual_events;
  if(result.control_valid&&result.mode!=DRIVE_STOP){
   rc=xd_command_input(automatic?"perception.result":"drive.input");if(rc)break;
   int encoded=xm_drive_command_json(json,sizeof(json),result.generation,result.v_mps,result.steer_rad);
   xd_command(&client,events,json,encoded,result.generation,result.v_mps,result.steer_rad,
    automatic?as:ms,cs,automatic?aa:ma,
    automatic?controller.auto_cfg.perception_timeout_ms:controller.manual_cfg.input_timeout_ms);
  }
  if(result.auto_reason!=last_reason){
   if(result.locally_active&&automatic&&seen_perception&&!xd_stopping())announce(auto_events,last_reason,&result);
   seen_perception=seen_perception||result.auto_reason!=AUTO_REASON_NO_PERCEPTION;
   last_reason=result.auto_reason;
  }
  int64_t tick=k_uptime_get();
  if(!io&&tick>=next_beat&&!xd_stopping()){
   int n=xm_app_heartbeat_json(json,sizeof(json),result.auto_state,tick-start);
   if(n>0)(void)xd_publish(&client,auto_events,"app.heartbeat",json);
   n=xm_app_heartbeat_json(json,sizeof(json),result.manual_state,tick-start);
   if(n>0)(void)xd_publish(&client,manual_events,"app.heartbeat",json);
   next_beat+=((tick-next_beat)/1000+1)*1000;
  }
  if((rc=xdiag_clock_us(&now)))break;
  int32_t wait=now<xd_next_release_us()?(int32_t)((xd_next_release_us()-now+999)/1000):0;
  if(!xd_stopping())k_msleep(wait);
  xd_iteration_end();
 }
 return xd_finish(&client,rc);
}
