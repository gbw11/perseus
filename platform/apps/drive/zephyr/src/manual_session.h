/* SPDX-License-Identifier: Apache-2.0 */
/* Worker-owned causal latch. Sample/action order belongs to the original
 * driver session; repeated MQTT delivery never creates a new action. */
#ifndef XENOVA_DRIVE_MANUAL_SESSION_H
#define XENOVA_DRIVE_MANUAL_SESSION_H
#include <xenova/mqtt/xm.h>
#include <string.h>
#include <errno.h>
struct drive_manual_gate {
 char session[XM_UUID_LEN+1];uint32_t generation;
 struct xm_manual_sample last;
 uint64_t neutral_action;
 bool seen,neutral,require_neutral,allowed,blocked;
};
static inline int drive_manual_begin(struct drive_manual_gate *g,const struct xm_vehicle_control *control)
{
 if(g->generation==control->generation){
  return strcmp(g->session,control->manual_session)||g->require_neutral!=!strcmp(control->manual_policy,"NEUTRAL_THEN_ACTION")?-EXDEV:0;
 }
 if(g->generation>control->generation)return -EXDEV;
 *g=(struct drive_manual_gate){.generation=control->generation,
  .require_neutral=!strcmp(control->manual_policy,"NEUTRAL_THEN_ACTION")};
 strcpy(g->session,control->manual_session);return 0;
}
static inline int drive_manual_observe(struct drive_manual_gate *g,const struct xm_manual_sample *s,int64_t received_age_ms)
{
 if(g->blocked||strcmp(g->session,s->session)||g->generation!=s->generation)return -EXDEV;
 if(!s->valid){g->blocked=true;g->allowed=false;return -ENODATA;}
 if(received_age_ms<0||(uint64_t)received_age_ms+s->age_ms>200)return -ETIME;
 if(g->seen){
  if(s->sample<g->last.sample||s->action<g->last.action)return -EXDEV;
  if(s->sample==g->last.sample)return s->action==g->last.action&&s->keys==g->last.keys&&
    s->age_ms==g->last.age_ms?0:-EXDEV;
  if(s->action==g->last.action&&s->keys!=g->last.keys)return -EXDEV;
 }
 g->last=*s;g->seen=true;
 if(!g->require_neutral)g->allowed=true;
 else if(!s->keys){g->neutral=true;g->neutral_action=s->action;g->allowed=false;}
 else if(g->neutral&&s->action>g->neutral_action)g->allowed=true;
 return 0;
}
#endif
