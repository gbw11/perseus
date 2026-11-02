/* SPDX-License-Identifier: Apache-2.0 */
/* App-owned state preparation. Work runs outside metadata locks; only a fully
 * validated immutable copy is published to an adapter and kept through join.
 * These stamps are not output authority or a state-transfer protocol. */
#ifndef XENOVA_DRIVE_PREPARATION_H
#define XENOVA_DRIVE_PREPARATION_H
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
#include <xenova/mqtt/xm.h>

struct drive_state_context {
 char request[XM_UUID_LEN+1],execution[XM_UUID_LEN+1],instance[XM_UUID_LEN+1];
 uint64_t frame,configuration,maneuver;
};
enum drive_state_phase { DRIVE_STATE_EMPTY,DRIVE_STATE_WORKING,DRIVE_STATE_COMPLETE,DRIVE_STATE_CANCELLED };
enum drive_state_origin { DRIVE_STATE_REBUILT,DRIVE_STATE_HANDOFF };
struct drive_state_source {
 char vm[XM_UUID_LEN+1],execution[XM_UUID_LEN+1],instance[XM_UUID_LEN+1];
 uint32_t generation;
};
struct drive_state_preparation {
 struct drive_state_context context;
 enum drive_state_phase phase;
 enum drive_state_origin origin;
 uint64_t deadline_ms,state_revision;
 /* Captured provenance: an old accepted state is not an old command/authority. */
 struct drive_state_source source;
 bool functionally_valid;
};
static inline bool drive_state_uuid(const char *id)
{
 bool nonzero=false;
 for(unsigned i=0;i<XM_UUID_LEN;i++){
  if(i==8||i==13||i==18||i==23){if(id[i]!='-')return false;}
  else{if(!((id[i]>='0'&&id[i]<='9')||(id[i]>='a'&&id[i]<='f')))return false;nonzero|=id[i]!='0';}
 }
 return !id[XM_UUID_LEN]&&nonzero;
}
static inline bool drive_state_context_equal(const struct drive_state_context *a,const struct drive_state_context *b)
{
 return a&&b&&!strcmp(a->request,b->request)&&!strcmp(a->execution,b->execution)&&
  !strcmp(a->instance,b->instance)&&a->frame==b->frame&&a->configuration==b->configuration&&a->maneuver==b->maneuver;
}
static inline int drive_state_begin(struct drive_state_preparation *p,const struct drive_state_context *context,
 uint64_t now_ms,uint64_t deadline_ms)
{
 if(!p||!context||!drive_state_uuid(context->request)||!drive_state_uuid(context->execution)||
    !drive_state_uuid(context->instance))return -EINVAL;
 if(p->phase!=DRIVE_STATE_EMPTY){
  if(!drive_state_context_equal(&p->context,context)||p->deadline_ms!=deadline_ms)return -EXDEV;
  if(p->phase==DRIVE_STATE_CANCELLED)return -ECANCELED;
  if(p->phase==DRIVE_STATE_COMPLETE)return 0;
  if(now_ms>=deadline_ms){p->phase=DRIVE_STATE_CANCELLED;return -ETIMEDOUT;}
  return 0;
 }
 if(now_ms>=deadline_ms)return -ETIMEDOUT;
 *p=(struct drive_state_preparation){.context=*context,.phase=DRIVE_STATE_WORKING,.deadline_ms=deadline_ms};
 return 0;
}
static inline int drive_state_publish(struct drive_state_preparation *p,const struct drive_state_context *work,
 uint64_t now_ms,enum drive_state_origin origin,uint64_t revision,const struct drive_state_source *source,bool valid)
{
 if(!p||!drive_state_context_equal(&p->context,work))return -EXDEV;
 if(p->phase!=DRIVE_STATE_WORKING)return -ECANCELED;
 if(now_ms>=p->deadline_ms){p->phase=DRIVE_STATE_CANCELLED;return -ETIMEDOUT;}
 if(!valid||!revision||(origin!=DRIVE_STATE_REBUILT&&origin!=DRIVE_STATE_HANDOFF)||
    (origin==DRIVE_STATE_HANDOFF&&(!source||!source->generation||!drive_state_uuid(source->vm)||
     !drive_state_uuid(source->execution)||!drive_state_uuid(source->instance))))return -EINVAL;
 /* The transport must have authenticated and accepted this source first.
  * Copying these identifiers is provenance preservation, not authentication. */
 p->origin=origin;p->state_revision=revision;if(source)p->source=*source;
 p->functionally_valid=true;p->phase=DRIVE_STATE_COMPLETE;return 0;
}
static inline void drive_state_cancel(struct drive_state_preparation *p)
{
 if(p){p->functionally_valid=false;p->phase=DRIVE_STATE_CANCELLED;}
}
static inline bool drive_state_usable(const struct drive_state_preparation *p,const struct drive_state_context *current)
{
 /* Completion deadline is not a blanket TTL for a map/path/state copy.
  * The adapter must recheck current pose/input/functional compatibility. */
 return p&&p->phase==DRIVE_STATE_COMPLETE&&p->functionally_valid&&p->state_revision&&
  drive_state_context_equal(&p->context,current);
}
#endif
