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
 struct xd_manual_witness witness;
 struct xm_vehicle_control control;
 char receiver[XM_UUID_LEN+1];
 int64_t received_ms;
 bool seen,controlled,blocked;
};
static inline void xd_manual_bind(struct xd_manual_source *s,const char *receiver)
{
 if(strcmp(s->receiver,receiver)){
  memset(&s->witness,0,sizeof(s->witness));strcpy(s->receiver,receiver);
 }
 /* Keep original sample identity/time across app replacement and reconnect. */
}
static inline void xd_manual_control(struct xd_manual_source *s,struct xm_span payload)
{
 struct xm_vehicle_control c;
 if(xm_vehicle_control_parse(payload,&c)||!c.manual_policy[0]){
  s->controlled=false;memset(&s->witness,0,sizeof(s->witness));return;
 }
 if(s->controlled&&c.generation==s->control.generation){
  if(strcmp(c.manual_session,s->control.manual_session)||strcmp(c.manual_policy,s->control.manual_policy)){
   s->blocked=true;memset(&s->witness,0,sizeof(s->witness));
  }
  return;
 }
 if(s->controlled&&c.generation<s->control.generation){
  s->blocked=true;memset(&s->witness,0,sizeof(s->witness));return;
 }
 s->control=c;s->controlled=true;s->blocked=false;memset(&s->witness,0,sizeof(s->witness));
}
/* Nonzero means the original sample must not replace/renew the latest slot.
 * Invalid source-session notices remain observable and revoke neutral proof. */
static inline int xd_manual_accept(struct xd_manual_source *s,struct xm_span payload,int64_t now)
{
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
