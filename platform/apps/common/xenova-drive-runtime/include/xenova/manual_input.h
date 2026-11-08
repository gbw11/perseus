/* SPDX-License-Identifier: Apache-2.0 */
/* Receive-owner evidence only. No calculation progress or output permission.
 * The latest original sample and one transition-local neutral survive slot
 * coalescing; no input history, wall-clock comparison or replay queue exists. */
#ifndef XENOVA_MANUAL_INPUT_H
#define XENOVA_MANUAL_INPUT_H
#include <xenova/mqtt/xm.h>
#include <errno.h>
#include <string.h>
struct xd_manual_witness {
 struct xm_manual_sample neutral;
 char receiver[XM_UUID_LEN+1];
 int64_t received_ms;
 bool present;
};
struct xd_manual_source {
 struct xm_manual_sample last;
 struct xm_manual_input modern;
 char modern_raw[XM_MAX_DRIVING_BYTES];
 uint16_t modern_raw_length;
 struct xd_manual_witness witness;
 struct xm_vehicle_control control;
 char receiver[XM_UUID_LEN+1];
 int64_t received_ms;
 bool seen,controlled,blocked,modern_seen;
};
static inline void xd_manual_bind(struct xd_manual_source *s,const char *receiver)
{
 if(strcmp(s->receiver,receiver)){
  memset(&s->witness,0,sizeof(s->witness));strcpy(s->receiver,receiver);
 }
 /* Keep original sample identity/time across app replacement and reconnect. */
}
/* Return the existing parser result separately from the causal gate. The
 * optional observation never changes which controls/samples the gate accepts. */
static inline int xd_manual_control_observed(struct xd_manual_source *s,struct xm_span payload,
 struct xm_vehicle_control *observed)
{
 struct xm_vehicle_control c;int rc=xm_vehicle_control_parse(payload,&c);
 if(observed){if(!rc)*observed=c;else memset(observed,0,sizeof(*observed));}
 if(rc||!c.manual_policy[0]){
  s->controlled=false;memset(&s->witness,0,sizeof(s->witness));return rc;
 }
 if(s->controlled&&c.generation==s->control.generation){
  if(strcmp(c.manual_session,s->control.manual_session)||strcmp(c.manual_policy,s->control.manual_policy)){
   s->blocked=true;memset(&s->witness,0,sizeof(s->witness));
  }
  return 0;
 }
 if(s->controlled&&c.generation<s->control.generation){
  s->blocked=true;memset(&s->witness,0,sizeof(s->witness));return 0;
 }
 bool carry=s->controlled&&!strcmp(c.manual_policy,"CONTINUE_SESSION")&&
  !strcmp(c.manual_session,s->control.manual_session)&&!strcmp(c.mode,"MANUAL");
 s->control=c;s->controlled=true;if(!carry)s->blocked=false;
 memset(&s->witness,0,sizeof(s->witness));return 0;
}
static inline void xd_manual_control(struct xd_manual_source *s,struct xm_span payload)
{
 (void)xd_manual_control_observed(s,payload,NULL);
}
/* Reception owns ordering and immutable bytes only. CONTINUE permits retaining
 * the original generation; current registry/clock/Vehicle remain worker gates. */
static inline int xd_manual_modern_accept(struct xd_manual_source *s,const struct xm_manual_input *in,int64_t now)
{
 if(now<0)return -ETIME;
 if(s->controlled&&(strcmp(in->session,s->control.manual_session)||in->generation>s->control.generation||
    (in->generation!=s->control.generation&&strcmp(s->control.manual_policy,"CONTINUE_SESSION"))))return -EXDEV;
 bool reset=!in->valid&&!in->keys&&in->kind==XM_MANUAL_HELD&&!strcmp(in->reason,"REARM_REQUIRED");
 bool release=!in->valid&&!in->keys&&in->kind==XM_MANUAL_HELD&&!strcmp(in->reason,"USER_RELEASE");
 bool new_admission=!s->modern_seen||strcmp(s->modern.session,in->session)||
  strcmp(s->modern.input_epoch,in->input_epoch)||s->modern.grant_revision!=in->grant_revision;
 bool initial_continuation=!s->modern_seen&&s->controlled&&
  !strcmp(s->control.manual_policy,"CONTINUE_SESSION")&&!strcmp(in->session,s->control.manual_session)&&
  in->generation<=s->control.generation;
 if(s->modern_seen&&!strcmp(s->modern.session,in->session)){
  const struct xm_manual_input *old=&s->modern;
  if(in->state<old->state||in->action<old->action)return -EALREADY;
  if(in->state==old->state)return memcmp(in,old,sizeof(*in))?-EXDEV:-EALREADY;
  if(in->kind!=old->kind||in->profile!=old->profile||strcmp(in->source_instance,old->source_instance)||
     strcmp(in->device_instance,old->device_instance))return -EXDEV;
  if(new_admission&&(!reset||in->grant_revision<=old->grant_revision))return -EXDEV;
  if(in->valid){
   if(in->sample<=old->sample||in->source_pi_us<old->source_pi_us)return -EXDEV;
   if(in->keys&&in->action==old->action&&in->keys!=old->keys)return -EXDEV;
   if(in->kind==XM_MANUAL_PULSE&&in->keys&&in->action==old->action&&in->expires_pi_us!=old->expires_pi_us)return -EXDEV;
   if(in->kind==XM_MANUAL_HELD&&old->valid&&!new_admission&&in->source_pi_us>=old->expires_pi_us){
    s->blocked=true;return -ETIME;
   }
  }
 }
 if(new_admission&&in->kind==XM_MANUAL_HELD&&!reset&&!initial_continuation)return -ENODATA;
 if(new_admission&&reset)s->blocked=false;
 if(s->blocked&&in->valid)return -ENODATA;
 s->modern=*in;s->modern_seen=true;s->received_ms=now;
 memset(&s->witness,0,sizeof(s->witness));
 if(!in->valid&&!reset&&!release)s->blocked=true;
 return 0;
}
/* Nonzero means the original sample must not replace/renew the latest slot.
 * Invalid source-session notices remain observable and revoke neutral proof. */
static inline int xd_manual_accept(struct xd_manual_source *s,struct xm_span payload,int64_t now)
{
 struct xm_manual_input modern;int modern_rc=xm_manual_input_parse(payload,&modern);
 if(!modern_rc){
  if(s->modern_seen&&!strcmp(s->modern.session,modern.session)&&s->modern.state==modern.state&&
     s->modern_raw_length&&(s->modern_raw_length!=payload.len||memcmp(s->modern_raw,payload.p,payload.len)))return -EXDEV;
  int accepted=xd_manual_modern_accept(s,&modern,now);
  if(!accepted){memcpy(s->modern_raw,payload.p,payload.len);s->modern_raw_length=payload.len;}
  return accepted;
 }
 if(modern_rc!=-ENOMSG)return modern_rc;
 struct xm_manual_sample in;int rc=xm_manual_sample_parse(payload,&in);
 if(rc==-ENOMSG)return s->controlled?-EBADMSG:0; /* explicit legacy only */
 if(rc)return rc;
 if(now<0)return -ETIME;
 if(s->seen&&!strcmp(s->last.session,in.session)){
  if(in.sample<=s->last.sample||in.action<s->last.action)return -EALREADY;
  if(in.valid&&s->last.valid&&in.action==s->last.action&&in.keys!=s->last.keys)return -EXDEV;
 }
 if(s->controlled){
  if(in.generation!=s->control.generation||strcmp(in.session,s->control.manual_session))return -EXDEV;
  if(s->blocked&&in.valid)return -ENODATA;
 }
 if(s->seen&&strcmp(s->last.session,in.session))memset(&s->witness,0,sizeof(s->witness));
 s->last=in;s->seen=true;s->received_ms=now;
 if(!in.valid){s->blocked=true;memset(&s->witness,0,sizeof(s->witness));}
 else if(s->controlled&&!s->blocked&&!in.keys&&in.age_ms<=200){
  s->witness=(struct xd_manual_witness){.neutral=in,.received_ms=now,.present=true};
  strcpy(s->witness.receiver,s->receiver);
 }
 return 0;
}
#endif
