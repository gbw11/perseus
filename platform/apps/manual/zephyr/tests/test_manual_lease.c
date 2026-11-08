/* input2/3→원입력 수락→수동 계산→command2의 연결 시험. 실제 STOP/clock은
 * 명시 facts를 사용하는 합성 입력이며 물리 브레이크/무선 수락이 아니다. */
#include <assert.h>
#include <errno.h>
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include "manual.h"
#include <xenova/manual_input.h>

#define SID "11111111-1111-4111-8111-111111111111"
#define SOURCE "22222222-2222-4222-8222-222222222222"
#define EPOCH "33333333-3333-4333-8333-333333333333"
#define DEVICE "44444444-4444-4444-8444-444444444444"
#define NIL "00000000-0000-0000-0000-000000000000"
static struct xm_span sp(const char *s){return (struct xm_span){s,(uint16_t)strlen(s)};}
static struct xm_manual_input pulse(void)
{
 struct xm_manual_input s={.profile=2,.kind=XM_MANUAL_PULSE,.valid=true,.sample=1,.action=1,.state=1,
  .generation=4,.keys=XM_KEY_UP,.source_pi_us=1000000,.expires_pi_us=1200000};
 strcpy(s.session,SID);strcpy(s.reason,"USER_NUDGE");return s;
}
static struct xm_vehicle_control control(void)
{
 struct xm_vehicle_control c={.generation=4};strcpy(c.mode,"MANUAL");strcpy(c.active_app,"manual");
 strcpy(c.manual_session,SID);strcpy(c.manual_policy,"NEUTRAL_THEN_ACTION");return c;
}
static struct manual_lease_facts facts(void)
{
 return (struct manual_lease_facts){.current=true,.stop_ready=true,.stop_sequence=7,
  .now_pi_us=1010000,.command_until_pi_us=1100000};
}
static void pulse_flow(void)
{
 struct manual_cfg cfg;manual_cfg_default(&cfg);
 struct manual_lease_gate gate={0};struct manual_lease_command out;
 struct xm_manual_input s=pulse();struct xm_vehicle_control c=control();struct manual_lease_facts f=facts();
 assert(!manual_lease_step(&cfg,&gate,&s,&c,&f,&out)&&out.target.v_mps>0&&!out.stop);
 assert(out.valid_until_pi_us==1100000&&s.expires_pi_us==1200000);
 /* A/D only cannot become stopped steering motion. */
 ++s.action;++s.state;++s.sample;s.keys=XM_KEY_LEFT;strcpy(s.reason,"USER_NEUTRAL");
 assert(!manual_lease_step(&cfg,&gate,&s,&c,&f,&out)&&out.stop&&!out.target.steer_rad);
 /* A new reverse press before actual STOP readiness is discarded. */
 ++s.action;++s.state;++s.sample;s.keys=XM_KEY_DOWN;strcpy(s.reason,"USER_NUDGE");f.stop_ready=false;
 assert(manual_lease_step(&cfg,&gate,&s,&c,&f,&out)==-EAGAIN&&out.stop);
 f.stop_ready=true;f.stop_sequence=8;
 /* STOP 완료가 나중에 와도 이미 버린 원 action은 다시 실행하지 않는다. */
 assert(manual_lease_step(&cfg,&gate,&s,&c,&f,&out)==-ENODATA&&out.stop);
 ++s.action;++s.state;++s.sample;
 assert(!manual_lease_step(&cfg,&gate,&s,&c,&f,&out)&&out.target.v_mps<0);
 f.now_pi_us=s.expires_pi_us;f.command_until_pi_us=s.expires_pi_us;
 assert(manual_lease_step(&cfg,&gate,&s,&c,&f,&out)==-ETIME&&out.stop);
}
static void held_continuation(void)
{
 struct manual_cfg cfg;manual_cfg_default(&cfg);
 struct manual_lease_gate gate={0};struct manual_lease_command out;
 struct xm_manual_input s=pulse();struct xm_vehicle_control c=control();struct manual_lease_facts f=facts();
 s.profile=3;s.kind=XM_MANUAL_HELD;s.valid=false;s.keys=0;s.expires_pi_us=0;s.action=0;
 s.grant_revision=1;strcpy(s.reason,"REARM_REQUIRED");strcpy(s.source_instance,SOURCE);
 strcpy(s.input_epoch,EPOCH);strcpy(s.device_instance,DEVICE);
 assert(!manual_lease_step(&cfg,&gate,&s,&c,&f,&out)&&out.stop&&!gate.blocked);
 s.valid=true;s.keys=XM_KEY_UP;s.action=1;++s.state;++s.sample;s.expires_pi_us=1150000;strcpy(s.reason,"USER_KEYS");
 assert(!manual_lease_step(&cfg,&gate,&s,&c,&f,&out)&&!out.stop);
 struct xm_manual_input original=s;
 ++c.generation;strcpy(c.manual_policy,"CONTINUE_SESSION");f.continuation=true;
 f.stop_ready=false;f.stop_sequence=0;
 assert(!manual_lease_step(&cfg,&gate,&s,&c,&f,&out)&&!out.stop&&out.target.generation==5);
 assert(!memcmp(&s,&original,sizeof(s))&&s.generation==4&&s.expires_pi_us==1150000);
 /* A freshly activated B may consume actual continuation proof without an
  * invented new input generation, Enter or source sample. */
 struct manual_lease_gate standby={0};
 assert(!manual_lease_step(&cfg,&standby,&s,&c,&f,&out)&&!out.stop);
 assert(!standby.used_stop_sequence&&standby.continuation_closed);
 /* The same app cannot turn a fault, expired lease or reset-to-new-control
  * into a second first-entry carry. Original sample bytes stay untouched. */
 f.current=false;assert(manual_lease_step(&cfg,&standby,&s,&c,&f,&out)<0&&out.stop);
 f.current=true;assert(manual_lease_step(&cfg,&standby,&s,&c,&f,&out)<0&&out.stop);
 struct manual_lease_gate unavailable={0};f.current=false;
 assert(manual_lease_step(&cfg,&unavailable,&s,&c,&f,&out)<0&&!unavailable.seen&&unavailable.continuation_closed);
 f.current=true;assert(manual_lease_step(&cfg,&unavailable,&s,&c,&f,&out)<0&&out.stop);
 struct manual_lease_gate expired={0};f.now_pi_us=s.expires_pi_us;f.command_until_pi_us=s.expires_pi_us;
 assert(manual_lease_step(&cfg,&expired,&s,&c,&f,&out)==-ETIME&&out.stop);
 assert(expired.continuation_closed);
 ++s.state;++s.sample;s.source_pi_us=f.now_pi_us;s.expires_pi_us=f.now_pi_us+150000;
 f.command_until_pi_us=f.now_pi_us+100000;
 assert(manual_lease_step(&cfg,&expired,&s,&c,&f,&out)<0&&out.stop);
 struct xd_manual_source fresh_rx={.controlled=true,.control=c};
 assert(!xd_manual_modern_accept(&fresh_rx,&original,90)&&fresh_rx.modern.generation==4);
 struct xd_manual_source new_driver={.controlled=true,.control=c};
 strcpy(new_driver.control.manual_policy,"NEUTRAL_THEN_ACTION");
 assert(xd_manual_modern_accept(&new_driver,&original,90)==-EXDEV);
 new_driver.control.generation=original.generation;
 assert(xd_manual_modern_accept(&new_driver,&original,90)==-ENODATA);
 /* Release cannot be coalesced away in the receive owner: invalid remains
  * blocked until the registered higher-grant reset. */
 struct xd_manual_source rx={.controlled=true,.control=c};
 s=original;s.valid=false;s.keys=0;strcpy(s.reason,"REARM_REQUIRED");
 assert(!xd_manual_modern_accept(&rx,&s,100));
 ++s.state;++s.sample;++s.action;s.valid=true;s.keys=XM_KEY_UP;strcpy(s.reason,"USER_KEYS");
 assert(!xd_manual_modern_accept(&rx,&s,101));
 ++s.state;++s.action;s.valid=false;s.keys=0;strcpy(s.reason,"USER_RELEASE");
 assert(!xd_manual_modern_accept(&rx,&s,101)&&!rx.blocked);
 ++s.state;++s.sample;++s.action;s.valid=true;s.keys=XM_KEY_UP;strcpy(s.reason,"USER_KEYS");
 assert(!xd_manual_modern_accept(&rx,&s,101)&&!rx.blocked);
 ++s.state;s.valid=false;s.keys=0;strcpy(s.reason,"SOURCE_LOST");
 assert(!xd_manual_modern_accept(&rx,&s,102));
 ++s.state;++s.sample;s.valid=true;s.keys=XM_KEY_UP;strcpy(s.reason,"HELD_SAMPLE");
 assert(xd_manual_modern_accept(&rx,&s,103)<0&&rx.blocked);
 s.valid=false;s.keys=0;s.grant_revision=2;strcpy(s.input_epoch,DEVICE);strcpy(s.reason,"REARM_REQUIRED");
 assert(!xd_manual_modern_accept(&rx,&s,104));
 ++s.state;++s.sample;++s.action;s.valid=true;s.keys=XM_KEY_UP;strcpy(s.reason,"USER_KEYS");
 assert(!xd_manual_modern_accept(&rx,&s,105));

 struct manual_lease_gate released={0};struct xm_vehicle_control ordinary=control();
 struct manual_lease_facts actual=facts();s=original;
 assert(!manual_lease_step(&cfg,&released,&s,&ordinary,&actual,&out)&&!out.stop);
 ++s.state;++s.action;s.keys=0;s.valid=false;strcpy(s.reason,"USER_RELEASE");
 actual.stop_ready=false;actual.stop_sequence=0;
 assert(!manual_lease_step(&cfg,&released,&s,&ordinary,&actual,&out)&&out.stop&&!released.blocked);
 ++s.state;++s.sample;++s.action;s.keys=XM_KEY_UP;s.valid=true;strcpy(s.reason,"USER_KEYS");
 actual.stop_ready=true;actual.stop_sequence=8;
 assert(!manual_lease_step(&cfg,&released,&s,&ordinary,&actual,&out)&&!out.stop);
}
static void wire_flow(void)
{
 const char *p="{\"profile\":\"manual-input-2\",\"session_id\":\"" SID "\",\"producer_kind\":\"PULSE\","
  "\"sample_seq\":\"18446744073709551615\",\"action_seq\":\"1\",\"state_seq\":\"2\",\"generation\":4,"
  "\"keys\":[\"up\"],\"valid\":true,\"source_pi_us\":\"1000000\",\"action_expires_pi_us\":\"1200000\",\"state_reason\":\"USER_NUDGE\"}";
 struct xm_manual_input s;assert(!xm_manual_input_parse(sp(p),&s)&&s.sample==UINT64_MAX);
 const char *h="{\"profile\":\"manual-input-3\",\"session_id\":\"" SID "\",\"producer_kind\":\"HELD\","
  "\"sample_seq\":\"3\",\"action_seq\":\"1\",\"state_seq\":\"3\",\"generation\":4,\"keys\":[\"up\"],\"valid\":true,"
  "\"source_pi_us\":\"1000000\",\"action_expires_pi_us\":\"1150000\",\"state_reason\":\"HELD_SAMPLE\","
  "\"source_instance\":\"" SOURCE "\",\"input_epoch\":\"" EPOCH "\",\"device_instance\":\"" DEVICE "\","
  "\"grant_revision\":\"1\",\"key_event_seq\":\"1\"}";
 struct xm_manual_input held;assert(!xm_manual_input_parse(sp(h),&held)&&held.kind==XM_MANUAL_HELD);
 /* A same state changed raw packet cannot renew or conflict through the
  * normalized-value duplicate path, even if only JSON whitespace changes. */
 struct xd_manual_source raw={0};
 const char *reset="{\"profile\":\"manual-input-2\",\"session_id\":\"" SID "\",\"producer_kind\":\"NEUTRAL_ONLY\","
  "\"sample_seq\":\"1\",\"action_seq\":\"0\",\"state_seq\":\"1\",\"generation\":4,\"keys\":[],\"valid\":true,"
  "\"source_pi_us\":\"1000000\",\"action_expires_pi_us\":\"0\",\"state_reason\":\"NEUTRAL\"}";
 assert(!xd_manual_accept(&raw,sp(reset),1));
 assert(xd_manual_accept(&raw,sp(reset),2)==-EALREADY&&raw.received_ms==1);
 char altered[1024];snprintf(altered,sizeof(altered)," %s",reset);
 assert(xd_manual_accept(&raw,sp(altered),3)==-EXDEV&&raw.received_ms==1);
 struct xm_drive_command2 v={.generation=4,.v_mps=.2f,.vehicle_sequence=9,.calculation_sequence=12,
  .completed_guest_us=222,.valid_until_pi_us=1100000,.driver_action_seq=s.action,.driver_state_seq=s.state};
 strcpy(v.producer_role,"MANUAL");strcpy(v.command_kind,"DRIVE");strcpy(v.mission_id,NIL);strcpy(v.driver_session,SID);
 char json[XM_COMMAND2_BYTES+1];int n=xm_drive_command2_json(json,sizeof(json),&v);assert(n>0);
 struct xm_drive_command2 round;assert(!xm_drive_command2_parse(sp(json),&round));
 assert(round.valid_until_pi_us==v.valid_until_pi_us&&round.driver_state_seq==s.state);
 struct xm_drive_command3 clocked={.command=v,.clock_probe_sequence=81},round3;
 assert(xm_drive_command3_json(json,sizeof(json),&clocked)>0);
 assert(!xm_drive_command3_parse(sp(json),&round3)&&round3.clock_probe_sequence==81);
 assert(!round3.next_direction);
 assert(xm_drive_command2_parse(sp(json),&round)==-EINVAL);
 clocked.next_direction=1;assert(xm_drive_command3_json(json,sizeof(json),&clocked)==-EINVAL);
 clocked.next_direction=0;
 clocked.clock_probe_sequence=0;assert(xm_drive_command3_json(json,sizeof(json),&clocked)==-EINVAL);
 v.world_sequence=1;assert(xm_drive_command2_json(json,sizeof(json),&v)==-EINVAL);v.world_sequence=0;
 strcpy(v.command_kind,"STOP");v.v_mps=0;v.vehicle_sequence=0;v.valid_until_pi_us=0;
 assert(xm_drive_command2_json(json,sizeof(json),&v)>0);
 clocked.command=v;assert(xm_drive_command3_json(json,sizeof(json),&clocked)>0);
 assert(!xm_drive_command3_parse(sp(json),&round3)&&!round3.clock_probe_sequence);
 strcpy(clocked.command.producer_role,"AUTO");strcpy(clocked.command.driver_session,NIL);
 clocked.command.driver_action_seq=clocked.command.driver_state_seq=0;
 strcpy(clocked.command.mission_id,SID);clocked.command.mission_revision=1;clocked.command.grant_revision=1;
 clocked.command.plan_revision=clocked.command.phase_revision=1;clocked.command.world_sequence=1;
 clocked.next_direction=-1;
 assert(xm_drive_command3_json(json,sizeof(json),&clocked)>0);
 assert(!xm_drive_command3_parse(sp(json),&round3)&&round3.next_direction==-1);
 strcpy(clocked.command.command_kind,"DRIVE");clocked.clock_probe_sequence=81;
 clocked.command.vehicle_sequence=9;clocked.command.valid_until_pi_us=1100000;
 assert(xm_drive_command3_json(json,sizeof(json),&clocked)==-EINVAL);
 const char *bad="{\"profile\":\"manual-input-2\",\"keys\":[\"up\"]}";
 assert(xm_manual_input_parse(sp(bad),&s)==-EINVAL);
 struct manual_cfg cfg;manual_cfg_default(&cfg);struct manual_command legacy;const char *state;
 struct manual_inputs in={.input_payload=sp(p),.control_payload=sp("{\"generation\":4,\"mode\":\"MANUAL\",\"active_app\":\"manual\"}")};
 manual_step(&cfg,&in,&legacy,&state);assert(!legacy.v_mps&&!legacy.steer_rad);
}
/* Actual Python actor bytes can enter the existing native parser without a
 * second JSON producer. The ordinary lease vectors remain the no-arg mode. */
static int input_file(const char *path)
{
 FILE *file=fopen(path,"rb");if(!file)return 2;
 char raw[1025];size_t length=fread(raw,1,sizeof(raw),file);
 int failed=ferror(file);if(fclose(file))failed=1;
 if(failed||!length||length>1024)return 2;
 struct xm_manual_input s;
 if(xm_manual_input_parse((struct xm_span){raw,(uint16_t)length},&s))return 3;
 printf("{\"profile\":%u,\"kind\":%u,\"sample\":\"%" PRIu64 "\","
  "\"action\":\"%" PRIu64 "\",\"state\":\"%" PRIu64 "\",\"generation\":%u,"
  "\"keys\":%u,\"valid\":%s,\"source_pi_us\":\"%" PRIu64 "\","
  "\"expires_pi_us\":\"%" PRIu64 "\",\"session\":\"%s\","
  "\"source_instance\":\"%s\",\"input_epoch\":\"%s\",\"device_instance\":\"%s\","
  "\"grant_revision\":\"%" PRIu64 "\",\"key_event_seq\":\"%" PRIu64 "\",\"reason\":\"%s\"}\n",
  (unsigned)s.profile,(unsigned)s.kind,s.sample,s.action,s.state,(unsigned)s.generation,
  (unsigned)s.keys,s.valid?"true":"false",s.source_pi_us,s.expires_pi_us,s.session,
  s.source_instance,s.input_epoch,s.device_instance,s.grant_revision,s.key_event_seq,s.reason);
 return 0;
}
int main(int argc,char **argv)
{
 if(argc==3&&!strcmp(argv[1],"--input-file"))return input_file(argv[2]);
 if(argc!=1)return 2;
 pulse_flow();held_continuation();wire_flow();puts("manual lease flow PASS");return 0;
}
