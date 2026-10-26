/* SPDX-License-Identifier: Apache-2.0 */
#include "controller.h"
#include <string.h>

void drive_controller_init(struct drive_controller *c)
{
 memset(c,0,sizeof(*c));auto_cfg_default(&c->auto_cfg);manual_cfg_default(&c->manual_cfg);
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
      strcmp(v->active_vm_id,c->accepted.active_vm_id)))return false;
 }
 c->accepted=*v;c->seen_control=true;return true;
}
void drive_controller_step(struct drive_controller *c,const char *local_vm_id,
 struct xm_span control,struct xm_span input,int64_t input_age_ms,
 struct xm_span perception,int64_t perception_age_ms,struct drive_result *out)
{
 struct xm_vehicle_control v;
 memset(out,0,sizeof(*out));
 out->control_valid=control_valid(c,control,&v);
 struct xm_span accepted=out->control_valid?control:(struct xm_span){0};
 struct auto_inputs ai={.control_payload=accepted,.perception_payload=perception,.perception_age_ms=perception_age_ms};
 struct manual_inputs mi={.control_payload=accepted,.input_payload=input,.input_age_ms=input_age_ms};
 struct auto_command ac;struct auto_status as;struct manual_command mc;const char *ms;
 auto_step(&c->auto_cfg,&ai,&ac,&as);
 manual_step(&c->manual_cfg,&mi,&mc,&ms);
 out->auto_reason=as.reason;
 out->nearest_m=as.nearest_m;
 out->auto_state=out->manual_state="idle";
 if(!out->control_valid)return;
 out->generation=v.generation;
 out->locally_active=local_vm_id&&!strcmp(local_vm_id,v.active_vm_id);
 if(!strcmp(v.mode,"AUTO"))out->mode=DRIVE_AUTO;
 else if(!strcmp(v.mode,"MANUAL")||!strcmp(v.mode,"AUTO_READY"))out->mode=DRIVE_MANUAL;
 out->auto_state=as.reason==AUTO_REASON_NO_PERCEPTION?"error":
  (out->locally_active&&out->mode==DRIVE_AUTO?"active":"standby");
 uint32_t keys;
 bool input_ready=input_age_ms>=0&&input_age_ms<=c->manual_cfg.input_timeout_ms&&
  !xm_drive_input_parse(input,&keys);
 out->manual_state=!input_ready?"error":
  (out->locally_active&&out->mode==DRIVE_MANUAL?"active":"standby");
 if(out->mode==DRIVE_AUTO){out->v_mps=ac.v_mps;out->steer_rad=ac.steer_rad;}
 else if(out->mode==DRIVE_MANUAL){out->v_mps=mc.v_mps;out->steer_rad=mc.steer_rad;}
}
