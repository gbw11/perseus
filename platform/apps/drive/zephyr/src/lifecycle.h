/* SPDX-License-Identifier: Apache-2.0 */
/* Shared actual lifecycle classification; no identity, transport or permission. */
#ifndef XENOVA_DRIVE_LIFECYCLE_H
#define XENOVA_DRIVE_LIFECYCLE_H
#include "controller.h"
#include "workers.h"
struct drive_warm_view {const char *phase,*state,*reason;uint32_t generation;};
static inline bool drive_warm_current(const struct drive_task_status *status,const struct drive_completion *work,
 uint64_t context_revision,uint64_t connection_revision){
 return work->performed&&work->source.lifetime==status->lifetime&&work->context_revision==context_revision&&
  work->source.connection_revision==connection_revision;
}
static inline struct drive_warm_view drive_warm_classify(const struct drive_adapter *adapter,
 const struct drive_result *selected,const struct drive_task_status *status,const struct drive_completion *work,
 bool chosen,struct xm_span input,int64_t input_age,uint64_t context_revision,uint64_t connection_revision,
 int64_t tick,uint64_t clock,bool clock_valid){
 struct drive_warm_view v={chosen?"ACTIVATING":"WAITING","NOT_READY","CONTROL_UNAPPLIED",selected->control_valid?selected->generation:0};
 bool current=drive_warm_current(status,work,context_revision,connection_revision);
 if(selected->control_valid){
  v.reason=drive_adapter_qualify(adapter,input,input_age);
  if(!chosen){if(status->state==DRIVE_TASK_RUNNING||status->pending)v.reason="STATE_PREPARING";if(!v.reason)v.state="QUALIFIED";}
  else if(current){
   v.phase="EXECUTING";v.reason=work->sample.reason;
   if(!clock_valid||tick-work->finished_ms>=300||(status->state==DRIVE_TASK_RUNNING&&clock>status->running_deadline_us))v.reason="CALCULATION_FAILED";
   if(!v.reason)v.state="READY";
  }else if(!v.reason)v.reason="STATE_PREPARING";
 }
 if(status->revoked){v.phase=status->state==DRIVE_TASK_RESIDUAL?"RESIDUAL":"STOPPING";v.state="NOT_READY";
  v.reason=status->state==DRIVE_TASK_RESIDUAL?"RESOURCE_RETAINED":"APP_STOPPING";}
 return v;
}
#endif
