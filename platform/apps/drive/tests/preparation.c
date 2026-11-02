/* Actual adapters and common C contracts; only input/work completion is injected. */
#include "controller.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "xm_internal.h"
#define A "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
#define B "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
static struct xm_span span(const char *s){return (struct xm_span){s,strlen(s)};}
static void state_tests(void)
{
 struct drive_state_context context={.request=A,.execution=A,.instance=B,.frame=1,.configuration=2,.maneuver=3};
 struct drive_state_preparation work={0};
 struct drive_state_source source={.vm=A,.execution=A,.instance=B,.generation=41};
 assert(!drive_state_begin(&work,&context,0,500));
 assert(!drive_state_begin(&work,&context,10,500));
 assert(drive_state_begin(&work,&context,10,501)==-EXDEV);
 assert(!drive_state_publish(&work,&context,20,DRIVE_STATE_REBUILT,1,NULL,true));
 assert(drive_state_usable(&work,&context)); /* no peer copy needed at first start */
 assert(!drive_state_begin(&work,&context,900,500)); /* original completion, no new work */
 assert(drive_state_usable(&work,&context)); /* completion budget is not a blanket state TTL */
 struct drive_state_context changed=context;changed.maneuver++;
 assert(!drive_state_usable(&work,&changed));
 changed=context;strcpy(changed.instance,A);assert(!drive_state_usable(&work,&changed));
 drive_state_cancel(&work);assert(!drive_state_usable(&work,&context));
 assert(drive_state_publish(&work,&context,21,DRIVE_STATE_HANDOFF,2,&source,true)==-ECANCELED);
 struct drive_state_preparation expired={0};assert(!drive_state_begin(&expired,&context,0,50));
 assert(drive_state_publish(&expired,&context,50,DRIVE_STATE_HANDOFF,1,&source,true)==-ETIMEDOUT);
 struct drive_state_preparation handoff={0};assert(!drive_state_begin(&handoff,&context,0,50));
 assert(drive_state_publish(&handoff,&context,1,DRIVE_STATE_HANDOFF,1,NULL,true)==-EINVAL);
 assert(!drive_state_publish(&handoff,&context,2,DRIVE_STATE_HANDOFF,1,&source,true));
 assert(!strcmp(handoff.source.execution,A)&&handoff.source.generation==41);
 assert(drive_state_usable(&handoff,&context));
 puts("state preparation: rebuild, old accepted source, context/replay/cancel/deadline PASS");
}
static void manual_tests(void)
{
 const char *control="{\"generation\":42,\"mode\":\"MANUAL\",\"active_app\":\"manual\",\"active_vm_id\":\"" A
  "\",\"manual_policy\":\"NEUTRAL_THEN_ACTION\",\"manual_session\":\"" B "\"}";
 struct drive_adapter adapter;struct manual_cfg cfg;struct drive_sample output;char input[512];
 drive_manual_adapter(&adapter,&cfg);
 const char *keys[]={"\"up\"","","\"up\"","\"up\""};
 unsigned actions[]={3,4,4,5};
 for(unsigned i=0;i<4;i++){
  snprintf(input,sizeof(input),"{\"profile\":\"manual-session-1\",\"session_id\":\"%s\",\"sample_seq\":\"%u\","
   "\"action_seq\":\"%u\",\"generation\":42,\"keys\":[%s],\"valid\":true,\"age_ms\":0}",B,i+1,actions[i],keys[i]);
  assert(!drive_adapter_run(&adapter,span(control),span(input),0,&output));
  if(i<3)assert(output.v_mps==0);else assert(output.v_mps>0&&!output.reason);
 }
 assert(!drive_adapter_run(&adapter,span(control),span(input),201,&output));
 assert(output.v_mps==0&&!strcmp(output.reason,"INPUT_STALE"));
 /* A new MANUAL owner consumes current held input without requiring neutral. */
 char continued[512];snprintf(continued,sizeof(continued),"{\"generation\":42,\"mode\":\"MANUAL\",\"active_app\":\"manual\","
   "\"active_vm_id\":\"%s\",\"manual_policy\":\"CONTINUE_SESSION\",\"manual_session\":\"%s\"}",A,B);
 drive_manual_adapter(&adapter,&cfg);
 assert(!drive_adapter_run(&adapter,span(continued),span(input),0,&output)&&output.v_mps>0);
 snprintf(input,sizeof(input),"{\"profile\":\"manual-session-1\",\"session_id\":\"%s\",\"sample_seq\":\"9\","
  "\"action_seq\":\"5\",\"generation\":42,\"keys\":[],\"valid\":false,\"age_ms\":0}",B);
 assert(!strcmp(drive_adapter_qualify(&adapter,span(input),0),"INPUT_UNAVAILABLE"));
 assert(!drive_adapter_run(&adapter,span(continued),span(input),0,&output)&&output.v_mps==0&&
  !strcmp(output.reason,"INPUT_UNAVAILABLE"));
 struct drive_controller coordinator;struct drive_result selected;drive_controller_init(&coordinator);
 drive_controller_select(&coordinator,A,span(continued),&selected);assert(selected.control_valid);
 const char *downgrade="{\"generation\":42,\"mode\":\"MANUAL\",\"active_app\":\"manual\",\"active_vm_id\":\"" A "\"}";
 drive_controller_select(&coordinator,A,span(downgrade),&selected);assert(!selected.control_valid);
 puts("manual adapter: neutral/action causal order, no re-press on same-mode takeover, expiry PASS");
}
static void receive_tests(void)
{
 const char *control="{\"generation\":42,\"mode\":\"MANUAL\",\"active_app\":\"manual\",\"active_vm_id\":\"" A
  "\",\"manual_policy\":\"NEUTRAL_THEN_ACTION\",\"manual_session\":\"" B "\"}";
 const char *neutral="{\"profile\":\"manual-session-1\",\"session_id\":\"" B "\",\"sample_seq\":\"1\","
  "\"action_seq\":\"1\",\"generation\":42,\"keys\":[],\"valid\":true,\"age_ms\":0}";
 const char *action="{\"profile\":\"manual-session-1\",\"session_id\":\"" B "\",\"sample_seq\":\"2\","
  "\"action_seq\":\"2\",\"generation\":42,\"keys\":[\"up\"],\"valid\":true,\"age_ms\":0}";
 struct xd_manual_source source={0};xd_manual_bind(&source,B);xd_manual_control(&source,span(control));
 assert(!xd_manual_accept(&source,span(neutral),1000));
 assert(!xd_manual_accept(&source,span(action),1010));
 struct drive_adapter adapter;struct manual_cfg cfg;struct drive_sample output;
 drive_manual_adapter(&adapter,&cfg);
 /* One calculation after BOTH receives still has the original neutral proof. */
 assert(!drive_adapter_run_observed(&adapter,span(control),span(action),0,&source.witness,B,&output));
 assert(output.v_mps>0&&!output.reason&&adapter.progress==1);
 assert(xd_manual_accept(&source,span(action),1300)==-EALREADY&&source.received_ms==1010);
 assert(xd_manual_accept(&source,span(neutral),1300)==-EALREADY&&source.received_ms==1010);
 assert(!drive_adapter_run_observed(&adapter,span(control),span(action),290,&source.witness,B,&output));
 assert(!output.v_mps&&!strcmp(output.reason,"INPUT_STALE"));
 struct xd_manual_witness old=source.witness;
 xd_manual_bind(&source,A);assert(!source.witness.present&&source.received_ms==1010);
 assert(xd_manual_accept(&source,span(neutral),1300)==-EALREADY); /* app replacement cannot refresh old source */
 drive_manual_adapter(&adapter,&cfg);
 assert(!drive_adapter_run_observed(&adapter,span(control),span(action),0,&old,A,&output));
 assert(!output.v_mps&&!strcmp(output.reason,"INPUT_INVALID"));
 /* New genuine neutral/action samples belong to the replacement app only. */
 char n2[512],a2[512];strcpy(n2,neutral);strcpy(a2,action);
 char *at=strstr(n2,"sample_seq\":\"1");assert(at);at[strlen("sample_seq\":\"")]='3';
 at=strstr(a2,"sample_seq\":\"2");assert(at);at[strlen("sample_seq\":\"")]='4';
 at=strstr(n2,"action_seq\":\"1");assert(at);at[strlen("action_seq\":\"")]='3';
 at=strstr(a2,"action_seq\":\"2");assert(at);at[strlen("action_seq\":\"")]='4';
 assert(!xd_manual_accept(&source,span(n2),1310));assert(!xd_manual_accept(&source,span(a2),1320));
 drive_manual_adapter(&adapter,&cfg);
 assert(!drive_adapter_run_observed(&adapter,span(control),span(a2),0,&source.witness,A,&output)&&output.v_mps>0);
 char invalid[512];strcpy(invalid,a2);at=strstr(invalid,"sample_seq\":\"4");at[strlen("sample_seq\":\"")]='5';
 at=strstr(invalid,"true");memmove(at+5,at+4,strlen(at+4)+1);memcpy(at,"false",5);
 assert(!xd_manual_accept(&source,span(invalid),1330)&&!source.witness.present);
 at=strstr(a2,"sample_seq\":\"4");at[strlen("sample_seq\":\"")]='6';
 assert(xd_manual_accept(&source,span(a2),1340)==-ENODATA); /* invalid notice cannot be coalesced away */
 puts("receive evidence: coalesced neutral, duplicate/reversal, original age, app replacement, invalid-session latch PASS");
}

static void vectors(const char *path)
{
 FILE *file=fopen(path,"r");assert(file);char line[2048];unsigned count=0;
 while(fgets(line,sizeof(line),file)){
  struct xm_json json;assert(!xm_json_parse(&json,line,strlen(line)));bool expected;
  assert(xm_json_is_bool(&json,xm_json_get(&json,0,"accepted"),&expected));
  struct xm_manual_sample sample;
  assert((xm_manual_sample_parse(xm_json_span(&json,xm_json_get(&json,0,"payload")),&sample)==0)==expected);
  count++;
 }
 fclose(file);assert(count>=10);printf("shared manual vectors: %u PASS\n",count);
}
int main(int argc,char **argv){state_tests();manual_tests();receive_tests();assert(argc==2);vectors(argv[1]);}
