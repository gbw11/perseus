/* 수동주행 앱 로직 — Python xenova_manual/keymap.py·app.py 와 같은 규칙 (tests/test_manual.c 로 대조) */
#include <errno.h>
#include <string.h>

#include "manual.h"

void manual_cfg_default(struct manual_cfg *cfg)
{
	/* 실차 측정 전 값. 1/10 RC카 실내 시연 기준 (Python ManualSettings 기본값과 같다) */
	cfg->max_speed_mps = 1.0f;
	cfg->max_reverse_mps = 0.5f;
	cfg->max_steer_rad = 0.4f;
	cfg->input_timeout_ms = MANUAL_INPUT_TIMEOUT_MS;
}

void manual_target(const struct manual_cfg *cfg, uint32_t keys, float *v_mps, float *steer_rad)
{
	bool up = keys & XM_KEY_UP, down = keys & XM_KEY_DOWN;
	bool left = keys & XM_KEY_LEFT, right = keys & XM_KEY_RIGHT;

	if ((keys & XM_KEY_BRAKE) || (up && down)) {
		*v_mps = 0.0f;
	} else {
		*v_mps = (up ? cfg->max_speed_mps : 0.0f) - (down ? cfg->max_reverse_mps : 0.0f);
	}
	*steer_rad = (left ? cfg->max_steer_rad : 0.0f) - (right ? cfg->max_steer_rad : 0.0f);
}

void manual_step(const struct manual_cfg *cfg, const struct manual_inputs *in, struct manual_command *out,
		 const char **state)
{
	struct xm_vehicle_control control;
	uint32_t keys = 0;

	out->v_mps = 0.0f;
	out->steer_rad = 0.0f;
	struct xm_manual_input modern;
	int modern_rc = xm_manual_input_parse(in->input_payload, &modern);
	if (in->input_payload.p != NULL && in->input_age_ms <= cfg->input_timeout_ms &&
	    modern_rc == -ENOMSG &&
	    xm_drive_input_parse(in->input_payload, &keys) == 0) {
		manual_target(cfg, keys, &out->v_mps, &out->steer_rad);
	}

	if (in->control_payload.p != NULL && xm_vehicle_control_parse(in->control_payload, &control) == 0) {
		out->generation = control.generation;
		*state = strcmp(control.active_app, MANUAL_APP_ID) == 0 ? "active" : "standby";
	} else {
		out->generation = 0; /* ESP32 는 세대가 다르면 버리므로 차는 움직이지 않는다 */
		*state = "idle";
	}
}

static bool same_source(const struct xm_manual_input *a,const struct xm_manual_input *b)
{
 return a->profile==b->profile&&a->kind==b->kind&&!strcmp(a->session,b->session)&&
  !strcmp(a->source_instance,b->source_instance)&&!strcmp(a->device_instance,b->device_instance);
}
static bool same_admission(const struct xm_manual_input *a,const struct xm_manual_input *b)
{
 return !strcmp(a->input_epoch,b->input_epoch)&&a->grant_revision==b->grant_revision;
}
static int lease_stop(struct manual_lease_gate *g,struct manual_lease_command *out,int error,bool block)
{
 out->target.v_mps=out->target.steer_rad=0;out->stop=true;
 g->running=false;g->waiting_stop=true;g->blocked|=block;g->continuation_closed=true;
 return error;
}
int manual_lease_step(const struct manual_cfg *cfg,struct manual_lease_gate *g,const struct xm_manual_input *s,
 const struct xm_vehicle_control *control,const struct manual_lease_facts *f,struct manual_lease_command *out)
{
 if(!cfg||!g||!s||!control||!f||!out)return -EINVAL;
 *out=(struct manual_lease_command){.target.generation=control->generation,.stop=true};
 bool continuing=(!g->seen||same_source(&g->last,s))&&!strcmp(control->manual_policy,"CONTINUE_SESSION")&&f->continuation;
 bool first_continuation=continuing&&!g->seen&&!g->continuation_closed;
 if(!f->current||strcmp(control->mode,"MANUAL")||strcmp(control->active_app,MANUAL_APP_ID)||
    strcmp(control->manual_session,s->session)||!control->generation||
    s->generation>control->generation||
    (s->generation!=control->generation&&!continuing))return lease_stop(g,out,-EXDEV,false);
 if(g->generation>control->generation)return lease_stop(g,out,-EXDEV,true);
 if(g->generation&&g->generation!=control->generation&&!continuing){
  bool closed=g->continuation_closed;
  *g=(struct manual_lease_gate){.generation=control->generation,.waiting_stop=true,.continuation_closed=closed};
 }
 g->generation=control->generation;
 bool admitted_reset=!g->seen;
 if(g->seen){
  if(!same_source(&g->last,s)){
   /* A source change is a new selected driver context, never keyset fallback. */
   return lease_stop(g,out,-EXDEV,true);
  }
  if(s->state<g->last.state||s->action<g->last.action)return lease_stop(g,out,-EALREADY,false);
  if(s->state==g->last.state&&memcmp(s,&g->last,sizeof(*s)))return lease_stop(g,out,-EXDEV,true);
  if(s->state>g->last.state&&s->valid&&s->sample<=g->last.sample)return lease_stop(g,out,-EXDEV,true);
  if(s->action==g->last.action&&s->valid&&s->keys&&s->keys!=g->last.keys)return lease_stop(g,out,-EXDEV,true);
  if(s->kind==XM_MANUAL_PULSE&&s->keys&&s->action==g->last.action&&
     s->expires_pi_us!=g->last.expires_pi_us)return lease_stop(g,out,-EXDEV,true);
  if(!same_admission(&g->last,s)){
   bool reset=s->kind==XM_MANUAL_HELD&&s->state>g->last.state&&!s->valid&&!s->keys&&
    s->grant_revision>g->last.grant_revision&&!strcmp(s->reason,"REARM_REQUIRED");
   if(!reset)return lease_stop(g,out,-EXDEV,true);
   admitted_reset=true;
   g->blocked=false;g->rejected_action=0;g->waiting_stop=true;g->running=false;
  }
  /* A current HELD source that actually lost its lease cannot be revived by a
   * later sample. A stale result alone does not modify a newer source here. */
  if(s->kind==XM_MANUAL_HELD&&s->valid&&g->last.valid&&s->state>g->last.state&&
     s->source_pi_us>=g->last.expires_pi_us&&same_admission(&g->last,s))return lease_stop(g,out,-ETIME,true);
 }
 bool fresh=!g->seen||s->state>g->last.state;
 bool reset=s->kind==XM_MANUAL_HELD&&!s->valid&&!s->keys&&!strcmp(s->reason,"REARM_REQUIRED");
 bool release=s->kind==XM_MANUAL_HELD&&!s->valid&&!s->keys&&!strcmp(s->reason,"USER_RELEASE");
 if(fresh){g->last=*s;g->seen=true;g->continuation_closed=true;}
 if(!s->valid){
  if(reset&&admitted_reset)g->blocked=false;
  return lease_stop(g,out,(release||reset)&&!g->blocked?0:-ENODATA,!reset&&!release);
 }
 if(g->blocked)return lease_stop(g,out,-ENODATA,true);
 bool up=(s->keys&XM_KEY_UP)!=0,down=(s->keys&XM_KEY_DOWN)!=0;
 bool left=(s->keys&XM_KEY_LEFT)!=0,right=(s->keys&XM_KEY_RIGHT)!=0;
 if((up&&down)||(left&&right)||(s->keys&XM_KEY_BRAKE))return lease_stop(g,out,-EINVAL,s->kind==XM_MANUAL_HELD);
 int direction=up?1:down?-1:0;
 if(!direction||s->kind==XM_MANUAL_NEUTRAL_ONLY){
  /* A/D-only is STOP/steering hold, not an in-place servo request. */
  if(g->running){g->running=false;g->waiting_stop=true;}
  if(f->stop_ready)g->waiting_stop=false;
  return 0;
 }
 if(s->source_pi_us>f->now_pi_us||s->expires_pi_us<=f->now_pi_us||
    !f->command_until_pi_us||f->command_until_pi_us<=f->now_pi_us||
    f->command_until_pi_us>s->expires_pi_us||f->command_until_pi_us-f->now_pi_us>100000)return lease_stop(g,out,-ETIME,false);
 if(s->action<=g->rejected_action)return lease_stop(g,out,-ENODATA,false);
 bool reverse=g->direction&&g->direction!=direction;
 if(!g->running||reverse){
  /* A prepared B's first exact same-SID continuation has current Gateway
   * registry/input/clock/Vehicle proof. It does not invent a serial STOP or
   * turn moving feedback into stationary. Faults/fences/expiry spend this
   * one first-entry allowance for the whole app execution. */
  if(!first_continuation&&(!f->stop_ready||!f->stop_sequence||
     (g->direction&&f->stop_sequence==g->used_stop_sequence))){
   g->rejected_action=s->action;
   return lease_stop(g,out,-EAGAIN,false);
  }
  if(!first_continuation)g->used_stop_sequence=f->stop_sequence;
 }
 g->running=true;g->waiting_stop=false;g->direction=direction;
 manual_target(cfg,s->keys,&out->target.v_mps,&out->target.steer_rad);
 out->valid_until_pi_us=f->command_until_pi_us;out->stop=false;
 return 0;
}

int manual_vm_id_from_xenstore(const char *path, char out[XM_UUID_LEN + 1])
{
	static const char prefix[] = "/vm/";

	if (path == NULL || strncmp(path, prefix, sizeof(prefix) - 1) != 0) {
		return -EINVAL;
	}
	const char *uuid = path + sizeof(prefix) - 1;

	if (strlen(uuid) != XM_UUID_LEN) {
		return -EINVAL;
	}
	for (int i = 0; i < XM_UUID_LEN; i++) {
		char c = uuid[i];
		bool dash = i == 8 || i == 13 || i == 18 || i == 23;

		if (dash ? c != '-' : !((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F'))) {
			return -EINVAL;
		}
		out[i] = (c >= 'A' && c <= 'F') ? (char)(c - 'A' + 'a') : c;
	}
	out[XM_UUID_LEN] = '\0';
	if (strcmp(out, "00000000-0000-0000-0000-000000000000") == 0) {
		return -EINVAL; /* nil UUID 는 Domain-0 몫 (에이전트와 같은 규칙) */
	}
	return 0;
}
