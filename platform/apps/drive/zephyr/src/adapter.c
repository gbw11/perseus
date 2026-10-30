/* SPDX-License-Identifier: Apache-2.0 */
/* Existing stateless algorithms. Replacements keep the adapter contract. */
#include "adapter.h"
#include <errno.h>
#include <string.h>

static int automatic(void *context,struct xm_span control,struct xm_span input,int64_t age,struct drive_sample *out)
{
 struct auto_cfg *cfg=context;struct auto_command cmd;struct auto_status status;
 struct auto_inputs in={.control_payload=control,.perception_payload=input,.perception_age_ms=age};
 auto_step(cfg,&in,&cmd,&status);
 out->v_mps=cmd.v_mps;out->steer_rad=cmd.steer_rad;out->nearest_m=status.nearest_m;out->auto_reason=status.reason;
 struct xm_perception_result parsed;
 if(!input.p)out->reason="INPUT_UNAVAILABLE";
 else if(age<0||age>cfg->perception_timeout_ms)out->reason="INPUT_STALE";
 else if(xm_perception_result_parse(input,&parsed))out->reason="INPUT_INVALID";
 return 0;
}
static int manual(void *context,struct xm_span control,struct xm_span input,int64_t age,struct drive_sample *out)
{
 struct manual_cfg *cfg=context;struct manual_command cmd;const char *state;
 struct manual_inputs in={.control_payload=control,.input_payload=input,.input_age_ms=age};
 manual_step(cfg,&in,&cmd,&state);out->v_mps=cmd.v_mps;out->steer_rad=cmd.steer_rad;
 uint32_t keys;
 if(!input.p)out->reason="INPUT_UNAVAILABLE";
 else if(age<0||age>cfg->input_timeout_ms)out->reason="INPUT_STALE";
 else if(xm_drive_input_parse(input,&keys))out->reason="INPUT_INVALID";
 return 0;
}
void drive_auto_adapter(struct drive_adapter *a,struct auto_cfg *cfg)
{
 auto_cfg_default(cfg);*a=(struct drive_adapter){.calculate=automatic,.context=cfg,
  .input_timeout_ms=cfg->perception_timeout_ms,.state_ready=true};
}
void drive_manual_adapter(struct drive_adapter *a,struct manual_cfg *cfg)
{
 manual_cfg_default(cfg);*a=(struct drive_adapter){.calculate=manual,.context=cfg,
  .input_timeout_ms=cfg->input_timeout_ms,.state_ready=true};
}
int drive_adapter_run(struct drive_adapter *a,struct xm_span control,struct xm_span input,int64_t age,struct drive_sample *out)
{
 memset(out,0,sizeof(*out));
 int rc=a->calculate?a->calculate(a->context,control,input,age,out):-ENOSYS;
 if(rc||a->progress==UINT64_MAX){out->reason="CALCULATION_FAILED";out->v_mps=out->steer_rad=0;return rc?rc:-EOVERFLOW;}
 ++a->progress;
 if(!__builtin_isfinite(out->v_mps)||!__builtin_isfinite(out->steer_rad)){
  out->reason="OUTPUT_INVALID";out->v_mps=out->steer_rad=0;return -ERANGE;
 }
 if(a->requires_state_sync&&!a->state_ready)out->reason="STATE_PREPARING";
 return 0;
}
