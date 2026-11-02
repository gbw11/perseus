/* SPDX-License-Identifier: Apache-2.0 */
#include "controller.h"
#include <string.h>

void drive_controller_init(struct drive_controller *c)
{
 memset(c,0,sizeof(*c));drive_auto_adapter(&c->automatic,&c->auto_cfg);drive_manual_adapter(&c->manual,&c->manual_cfg);
}
void drive_controller_health(struct drive_controller *c,int (*clock)(uint64_t *),
 uint64_t deadline,uint64_t uptime,const struct xdr_snapshot *resources,int resource_rc)
{
 c->clock_us=clock;c->deadline_us=deadline;c->resource_uptime_ms=uptime;
 c->resources=resources;c->resource_rc=resource_rc;
}
void drive_controller_deadline_missed(struct drive_result *r)
{
 if(!r->auto_health.restrict_function)r->auto_health.failure=XDH_DEADLINE;
 if(!r->manual_health.restrict_function)r->manual_health.failure=XDH_DEADLINE;
 r->auto_health.restrict_function=r->manual_health.restrict_function=true;
 if(!r->auto_not_ready||strcmp(r->auto_not_ready,"OUTPUT_INVALID"))r->auto_not_ready="CALCULATION_FAILED";
 if(!r->manual_not_ready||strcmp(r->manual_not_ready,"OUTPUT_INVALID"))r->manual_not_ready="CALCULATION_FAILED";
 r->auto_readiness=r->manual_readiness="NOT_READY";
 r->auto_state=r->manual_state="error";
 r->v_mps=r->steer_rad=0;
}
static void calculate(struct drive_controller *c,struct drive_adapter *adapter,
 struct xm_span control,struct xm_span input,int64_t age,struct drive_sample *sample,
 struct xdh_health *health)
{
 uint64_t begin=0,end=0;int clock_rc=c->clock_us?c->clock_us(&begin):0;
 int rc=drive_adapter_run(adapter,control,input,age,sample);
 if(!c->clock_us)return;
 int end_rc=c->clock_us(&end);
 struct xdh_work work={.state=XDH_COMPLETED,.required=true,.rc=rc,
  .clock_rc=clock_rc?clock_rc:end_rc,.begin_us=begin,.end_us=end,.deadline_us=c->deadline_us};
 (void)xdh_assess(&work,end,c->resource_uptime_ms,c->resources,c->resource_rc,health);
 if(health->restrict_function){
  if(!sample->reason||(strcmp(sample->reason,"OUTPUT_INVALID")&&strcmp(sample->reason,"CALCULATION_FAILED")))
   sample->reason="CALCULATION_FAILED";
  sample->v_mps=sample->steer_rad=0;
 }
}
static bool control_valid(struct drive_controller *c,struct xm_span payload,struct xm_vehicle_control *v)
{
 if(xm_vehicle_control_parse(payload,v))return false;
 bool safe=!strcmp(v->mode,"SAFE_STOP")&&!v->active_app[0]&&!v->active_vm_id[0];
 bool automatic=!strcmp(v->mode,"AUTO")&&!strcmp(v->active_app,AUTO_APP_ID);
 bool manual=(!strcmp(v->mode,"MANUAL")||!strcmp(v->mode,"AUTO_READY"))&&!strcmp(v->active_app,MANUAL_APP_ID);
 if(!safe&&(!(automatic||manual)||!v->active_vm_id[0]||
    !strcmp(v->active_vm_id,"00000000-0000-0000-0000-000000000000")))return false;
 if(c->seen_control){
  if(v->generation<c->accepted.generation)return false;
  if(v->generation==c->accepted.generation&&
     (strcmp(v->mode,c->accepted.mode)||strcmp(v->active_app,c->accepted.active_app)||
      strcmp(v->active_vm_id,c->accepted.active_vm_id)||strcmp(v->manual_session,c->accepted.manual_session)||
      strcmp(v->manual_policy,c->accepted.manual_policy)))return false;
 }
 c->accepted=*v;c->seen_control=true;return true;
}
void drive_controller_select(struct drive_controller *c,const char *local_vm_id,
 struct xm_span control,struct drive_result *out)
{
 struct xm_vehicle_control v;
 memset(out,0,sizeof(*out));
 out->control_valid=control_valid(c,control,&v);
 if(!out->control_valid)return;
 out->generation=v.generation;
 out->locally_active=local_vm_id&&!strcmp(local_vm_id,v.active_vm_id);
 if(!strcmp(v.mode,"AUTO"))out->mode=DRIVE_AUTO;
 else if(!strcmp(v.mode,"MANUAL")||!strcmp(v.mode,"AUTO_READY"))out->mode=DRIVE_MANUAL;
}
void drive_controller_step(struct drive_controller *c,const char *local_vm_id,
 struct xm_span control,struct xm_span input,int64_t input_age_ms,
 struct xm_span perception,int64_t perception_age_ms,struct drive_result *out)
{
 drive_controller_select(c,local_vm_id,control,out);
 struct xm_span accepted=out->control_valid?control:(struct xm_span){0};
 struct drive_sample ac,mc;
 calculate(c,&c->automatic,accepted,perception,perception_age_ms,&ac,&out->auto_health);
 calculate(c,&c->manual,accepted,input,input_age_ms,&mc,&out->manual_health);
 out->auto_progress=c->automatic.progress;out->manual_progress=c->manual.progress;
 out->auto_not_ready=out->control_valid?ac.reason:"CONTROL_UNAPPLIED";
 out->manual_not_ready=out->control_valid?mc.reason:"CONTROL_UNAPPLIED";
 out->auto_readiness=!out->auto_not_ready?"READY":!strcmp(out->auto_not_ready,"STATE_PREPARING")?"PREPARING":"NOT_READY";
 out->manual_readiness=!out->manual_not_ready?"READY":!strcmp(out->manual_not_ready,"STATE_PREPARING")?"PREPARING":"NOT_READY";
 out->auto_reason=ac.auto_reason;out->nearest_m=ac.nearest_m;
 out->auto_state=out->manual_state="idle";
 if(!out->control_valid)return;
 /* Input readiness is distinct from a failed calculation. */
 bool auto_failed=ac.reason&&(!strcmp(ac.reason,"CALCULATION_FAILED")||!strcmp(ac.reason,"OUTPUT_INVALID"));
 bool manual_failed=mc.reason&&(!strcmp(mc.reason,"CALCULATION_FAILED")||!strcmp(mc.reason,"OUTPUT_INVALID"));
 out->auto_state=auto_failed?"error":
  (out->locally_active&&out->mode==DRIVE_AUTO?"active":"standby");
 out->manual_state=manual_failed?"error":
  (out->locally_active&&out->mode==DRIVE_MANUAL?"active":"standby");
 if(out->mode==DRIVE_AUTO){out->v_mps=ac.v_mps;out->steer_rad=ac.steer_rad;}
 else if(out->mode==DRIVE_MANUAL){out->v_mps=mc.v_mps;out->steer_rad=mc.steer_rad;}
}
