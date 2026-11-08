/* 명시 manual-input-2/3와 command2의 bounded codec. 권위·수신시각·운전 허가는
 * 생성하지 않으며 모든 원 시각/식별자를 값으로 복사한다. legacy codec과 별도다. */
#include <errno.h>
#include <string.h>
#include "xm_internal.h"

static const char nil[]="00000000-0000-0000-0000-000000000000";
static bool whitespace(char c){return c==' '||c=='\t'||c=='\r'||c=='\n';}
static int before(const struct xm_json *j,int t){return j->tok[t].start-(j->tok[t].type==JSMN_STRING);}
static int after(const struct xm_json *j,int t){return j->tok[t].end+(j->tok[t].type==JSMN_STRING);}
static bool separator(const struct xm_json *j,int begin,int end,char expected)
{
 for(;begin<end&&whitespace(j->js[begin]);begin++){}
 if(expected){if(begin>=end||j->js[begin++]!=expected)return false;}
 for(;begin<end&&whitespace(j->js[begin]);begin++){}
 return begin==end;
}
/* jsmn tokenizes bounded spans but does not enforce every comma/colon. The
 * explicit profiles are flat objects, so validate separators without another
 * recursive decoder or changing the historical legacy JSON acceptance. */
static bool flat_object(const struct xm_json *j)
{
 int cursor=j->tok[0].start+1,t=1;
 if(!separator(j,0,j->tok[0].start,0))return false;
 for(int pair=0;pair<j->tok[0].size;pair++){
  if(t+1>=j->count||j->tok[t].type!=JSMN_STRING||!separator(j,cursor,before(j,t),pair?',':0)||
     !separator(j,after(j,t),before(j,t+1),':'))return false;
  cursor=after(j,t+1);t=xm_json_skip(j,t+1);
 }
 return separator(j,cursor,j->tok[0].end-1,0);
}
static int object(struct xm_json *j,struct xm_span p,const char *const *keys,size_t count)
{
 if(!p.p||p.len>XM_COMMAND2_BYTES||xm_json_parse(j,p.p,p.len)||!xm_json_is_object(j,0)||
    j->tok[0].size!=(int)count||!flat_object(j))return -EINVAL;
 for(size_t i=0;i<count;i++)if(xm_json_get(j,0,keys[i])<0)return -EINVAL;
 return 0;
}
static int text(const struct xm_json *j,const char *key,char *out,size_t size)
{
 int t=xm_json_get(j,0,key);
 return !xm_json_is_string(j,t)||xm_span_copy(out,size,xm_json_span(j,t))<0?-EINVAL:0;
}
static bool uuid(const char *s,bool nonzero)
{
 if(strlen(s)!=XM_UUID_LEN)return false;
 for(size_t i=0;i<XM_UUID_LEN;i++){
  if(i==8||i==13||i==18||i==23){if(s[i]!='-')return false;}
  else if(!((s[i]>='0'&&s[i]<='9')||(s[i]>='a'&&s[i]<='f')))return false;
 }
 return !nonzero||strcmp(s,nil);
}
static int counter(const struct xm_json *j,const char *key,uint64_t *out)
{
 int t=xm_json_get(j,0,key);if(!xm_json_is_string(j,t))return -EINVAL;
 struct xm_span s=xm_json_span(j,t);uint64_t value=0;
 if(!s.len||s.len>20||(s.len>1&&s.p[0]=='0'))return -EINVAL;
 for(size_t i=0;i<s.len;i++){
  if(s.p[i]<'0'||s.p[i]>'9'||value>(UINT64_MAX-(s.p[i]-'0'))/10)return -ERANGE;
  value=value*10+(s.p[i]-'0');
 }
 *out=value;return 0;
}
static int u32(const struct xm_json *j,const char *key,uint32_t *out)
{
 int64_t value;int t=xm_json_get(j,0,key);struct xm_span s=xm_json_span(j,t);
 if(xm_json_int(j,t,&value)||value<0||value>UINT32_MAX||!s.len||
    (s.len>1&&s.p[0]=='0')||s.p[0]=='-')return -EINVAL;
 *out=(uint32_t)value;return 0;
}
static bool oneof(const char *s,const char *const *choices,size_t n)
{
 for(size_t i=0;i<n;i++)if(!strcmp(s,choices[i]))return true;
 return false;
}
static int keys(const struct xm_json *j,uint32_t *out)
{
 static const char *const names[]={"up","down","left","right","brake"};
 int t=xm_json_get(j,0,"keys");*out=0;
 if(t<0||j->tok[t].type!=JSMN_ARRAY||j->tok[t].size>5)return -EINVAL;
 int i=t+1,cursor=j->tok[t].start+1;
 for(int k=0;k<j->tok[t].size;k++,i=xm_json_skip(j,i)){
  if(!separator(j,cursor,before(j,i),k?',':0))return -EINVAL;
  bool found=false;
  for(unsigned bit=0;bit<5;bit++)if(xm_json_is_string(j,i)&&xm_span_eq(xm_json_span(j,i),names[bit])){
   if(*out&(1u<<bit))return -EINVAL;
   *out|=1u<<bit;found=true;break;
  }
  if(!found)return -EINVAL;
  cursor=after(j,i);
 }
 return separator(j,cursor,j->tok[t].end-1,0)?0:-EINVAL;
}

static int number(const struct xm_json *j,const char *key,double *value)
{
 int t=xm_json_get(j,0,key);struct xm_span s=xm_json_span(j,t);size_t i=0;
 if(!s.p||!s.len)return -EINVAL;
 if(s.p[i]=='-')i++;
 if(i==s.len)return -EINVAL;
 if(s.p[i]=='0')i++;
 else{if(s.p[i]<'1'||s.p[i]>'9')return -EINVAL;while(i<s.len&&s.p[i]>='0'&&s.p[i]<='9')i++;}
 if(i<s.len&&s.p[i]=='.'){
  size_t start=++i;while(i<s.len&&s.p[i]>='0'&&s.p[i]<='9')i++;
  if(i==start)return -EINVAL;
 }
 if(i<s.len&&(s.p[i]=='e'||s.p[i]=='E')){
  i++;if(i<s.len&&(s.p[i]=='+'||s.p[i]=='-'))i++;
  size_t start=i;while(i<s.len&&s.p[i]>='0'&&s.p[i]<='9')i++;
  if(i==start)return -EINVAL;
 }
 return i==s.len?xm_json_number(j,t,value):-EINVAL;
}
int xm_manual_input_parse(struct xm_span payload,struct xm_manual_input *out)
{
 static const char *const fields[]={"profile","session_id","producer_kind","sample_seq","action_seq",
  "state_seq","generation","keys","valid","source_pi_us","action_expires_pi_us","state_reason"};
 static const char *const held[]={"profile","session_id","producer_kind","sample_seq","action_seq",
  "state_seq","generation","keys","valid","source_pi_us","action_expires_pi_us","state_reason",
  "source_instance","input_epoch","device_instance","grant_revision","key_event_seq"};
 static const char *const reasons2[]={"NEUTRAL","USER_NUDGE","LEASE_EXPIRED","USER_NEUTRAL","REVOKED"};
 static const char *const reasons3[]={"NEUTRAL","USER_KEYS","HELD_SAMPLE","USER_RELEASE","SOURCE_LOST",
  "LEASE_EXPIRED","REVOKED","REARM_REQUIRED"};
 struct xm_json j;char profile[24],kind[20];
 if(!out)return -EINVAL;
 memset(out,0,sizeof(*out));
 if(!payload.p||payload.len>1024||xm_json_parse(&j,payload.p,payload.len)||!xm_json_is_object(&j,0))return -EINVAL;
 int p=xm_json_get(&j,0,"profile");
 if(p<0)return -ENOMSG;
 if(text(&j,"profile",profile,sizeof(profile)))return -EINVAL;
 bool h=!strcmp(profile,"manual-input-3");
 if(!h&&strcmp(profile,"manual-input-2"))return !strcmp(profile,"manual-session-1")?-ENOMSG:-EINVAL;
 if(object(&j,payload,h?held:fields,h?17:12)||text(&j,"producer_kind",kind,sizeof(kind))||
    text(&j,"session_id",out->session,sizeof(out->session))||!uuid(out->session,true)||
    text(&j,"state_reason",out->reason,sizeof(out->reason))||
    counter(&j,"sample_seq",&out->sample)||counter(&j,"action_seq",&out->action)||
    counter(&j,"state_seq",&out->state)||!out->state||u32(&j,"generation",&out->generation)||!out->generation||
    counter(&j,"source_pi_us",&out->source_pi_us)||counter(&j,"action_expires_pi_us",&out->expires_pi_us)||
    keys(&j,&out->keys)||!xm_json_is_bool(&j,xm_json_get(&j,0,"valid"),&out->valid))return -EINVAL;
 out->profile=h?3:2;
 if(h){
  out->kind=XM_MANUAL_HELD;
  if(strcmp(kind,"HELD")||!oneof(out->reason,reasons3,8)||
     text(&j,"source_instance",out->source_instance,sizeof(out->source_instance))||!uuid(out->source_instance,true)||
     text(&j,"input_epoch",out->input_epoch,sizeof(out->input_epoch))||!uuid(out->input_epoch,true)||
     text(&j,"device_instance",out->device_instance,sizeof(out->device_instance))||!uuid(out->device_instance,true)||
     counter(&j,"grant_revision",&out->grant_revision)||!out->grant_revision||
     counter(&j,"key_event_seq",&out->key_event_seq))return -EINVAL;
  if(out->keys&&out->expires_pi_us>out->source_pi_us&&
     out->expires_pi_us-out->source_pi_us>150000)return -EINVAL;
 }else{
  if(!oneof(out->reason,reasons2,5))return -EINVAL;
  if(!strcmp(kind,"NEUTRAL_ONLY")){
   out->kind=XM_MANUAL_NEUTRAL_ONLY;
   if(out->keys||out->action||out->expires_pi_us||strcmp(out->reason,"NEUTRAL"))return -EINVAL;
  }else if(!strcmp(kind,"PULSE"))out->kind=XM_MANUAL_PULSE;
  else return -EINVAL;
 }
 if(!out->valid&&out->keys)return -EINVAL;
 if(out->keys&&(!out->action||!out->sample||!out->source_pi_us||out->expires_pi_us<=out->source_pi_us))return -EINVAL;
 return 0;
}

static bool command_valid(const struct xm_drive_command2 *v)
{
 if(!memchr(v->producer_role,0,sizeof(v->producer_role))||!memchr(v->command_kind,0,sizeof(v->command_kind))||
    !memchr(v->mission_id,0,sizeof(v->mission_id))||!memchr(v->driver_session,0,sizeof(v->driver_session))||
    !uuid(v->mission_id,false)||!uuid(v->driver_session,false)||
    v->v_mps!=v->v_mps||v->v_mps-v->v_mps!=0||v->steer_rad!=v->steer_rad||v->steer_rad-v->steer_rad!=0)return false;
 bool stop=!strcmp(v->command_kind,"STOP"),hold=!strcmp(v->command_kind,"HOLD_STEER");
 if(!stop&&!hold&&strcmp(v->command_kind,"DRIVE"))return false;
 if((stop&&(v->v_mps!=0||v->steer_rad!=0))||(hold&&v->v_mps!=0))return false;
 if(!strcmp(v->producer_role,"MANUAL")){
  if(strcmp(v->mission_id,nil)||v->mission_revision||v->grant_revision||v->world_sequence||v->plan_revision||
     v->phase_revision||v->segment_index||v->cusp_index)return false;
  if(!stop&&(!strcmp(v->driver_session,nil)||!v->driver_state_seq))return false;
 }else if(!strcmp(v->producer_role,"AUTO")){
  if(strcmp(v->driver_session,nil)||v->driver_action_seq||v->driver_state_seq)return false;
  if(!stop&&(!strcmp(v->mission_id,nil)||!v->mission_revision||!v->grant_revision||
     !v->world_sequence||!v->plan_revision))return false;
 }else return false;
 return stop||(v->generation&&v->calculation_sequence&&v->vehicle_sequence&&v->valid_until_pi_us);
}
static bool next_valid(const struct xm_drive_command2 *v,int32_t next)
{
 return next>=-1&&next<=1&&(!next||(!strcmp(v->producer_role,"AUTO")&&
  (!strcmp(v->command_kind,"STOP")||!strcmp(v->command_kind,"HOLD_STEER"))));
}
static int command_parse(struct xm_span p,struct xm_drive_command2 *v,uint64_t *probe,int32_t *next,bool third)
{
 static const char *const fields[]={"profile","generation","producer_role","command_kind","stop_reason",
  "v_mps","steer_rad","mission_id","mission_revision","grant_revision","calculation_sequence",
  "vehicle_sequence","world_sequence","plan_revision","phase_revision","segment_index","cusp_index",
  "completed_guest_us","valid_until_pi_us","driver_session","driver_action_seq","driver_state_seq","clock_probe_sequence","next_direction"};
 struct xm_json j;double speed,steer;
 if(!v)return -EINVAL;
 memset(v,0,sizeof(*v));
 if(object(&j,p,fields,third?24:22)||!xm_span_eq(xm_json_span(&j,xm_json_get(&j,0,"profile")),third?"drive-command-3":"drive-command-2")||
    text(&j,"producer_role",v->producer_role,sizeof(v->producer_role))||
    text(&j,"command_kind",v->command_kind,sizeof(v->command_kind))||
    text(&j,"mission_id",v->mission_id,sizeof(v->mission_id))||
    text(&j,"driver_session",v->driver_session,sizeof(v->driver_session))||
    u32(&j,"generation",&v->generation)||u32(&j,"stop_reason",&v->stop_reason)||
    u32(&j,"segment_index",&v->segment_index)||u32(&j,"cusp_index",&v->cusp_index)||
    number(&j,"v_mps",&speed)||number(&j,"steer_rad",&steer))return -EINVAL;
 v->v_mps=(float)speed;v->steer_rad=(float)steer;
#define READ(name) if(counter(&j,#name,&v->name))return -EINVAL
 READ(mission_revision);READ(grant_revision);READ(calculation_sequence);READ(vehicle_sequence);READ(world_sequence);
 READ(plan_revision);READ(phase_revision);READ(completed_guest_us);READ(valid_until_pi_us);
 READ(driver_action_seq);READ(driver_state_seq);
#undef READ
 if(third&&(counter(&j,"clock_probe_sequence",probe)||(!*probe&&strcmp(v->command_kind,"STOP"))))return -EINVAL;
 if(third){
  int64_t direction;int token=xm_json_get(&j,0,"next_direction");struct xm_span span=xm_json_span(&j,token);
  if(xm_json_int(&j,token,&direction)||direction< -1||direction>1||
     (!xm_span_eq(span,"-1")&&!xm_span_eq(span,"0")&&!xm_span_eq(span,"1")))return -EINVAL;
  *next=(int32_t)direction;if(!next_valid(v,*next))return -EINVAL;
 }
 return command_valid(v)?0:-EINVAL;
}
int xm_drive_command2_parse(struct xm_span p,struct xm_drive_command2 *v)
{
 return command_parse(p,v,NULL,NULL,false);
}
int xm_drive_command3_parse(struct xm_span p,struct xm_drive_command3 *v)
{
 if(!v)return -EINVAL;
 memset(v,0,sizeof(*v));return command_parse(p,&v->command,&v->clock_probe_sequence,&v->next_direction,true);
}
static int command_json(char *out,size_t n,const struct xm_drive_command2 *v,uint64_t probe,int32_t next,bool third)
{
 if(!out||!v||!command_valid(v))return -EINVAL;
 /* xm_put_fixed has a bounded integer workspace. Never silently clamp an
  * unrepresentable requested actuator target in the serializer. */
 if(v->v_mps>1e12f||v->v_mps< -1e12f||v->steer_rad>1e12f||v->steer_rad< -1e12f)return -ERANGE;
 if(third&&!probe&&strcmp(v->command_kind,"STOP"))return -EINVAL;
 if(third&&!next_valid(v,next))return -EINVAL;
 size_t pos=0;int rc=xm_put(out,n,&pos,third?"{\"profile\":\"drive-command-3\"":"{\"profile\":\"drive-command-2\"");
#define STR(name) do{rc|=xm_put(out,n,&pos,",\"" #name "\":");rc|=xm_put_json_string(out,n,&pos,v->name);}while(0)
#define NUM(name) do{rc|=xm_put(out,n,&pos,",\"" #name "\":");rc|=xm_put_uint(out,n,&pos,v->name);}while(0)
#define COUNT(name) do{rc|=xm_put(out,n,&pos,",\"" #name "\":\"");rc|=xm_put_uint(out,n,&pos,v->name);rc|=xm_put(out,n,&pos,"\"");}while(0)
 NUM(generation);STR(producer_role);STR(command_kind);NUM(stop_reason);
 rc|=xm_put(out,n,&pos,",\"v_mps\":");rc|=xm_put_fixed(out,n,&pos,v->v_mps,6);
 rc|=xm_put(out,n,&pos,",\"steer_rad\":");rc|=xm_put_fixed(out,n,&pos,v->steer_rad,6);
 STR(mission_id);COUNT(mission_revision);COUNT(grant_revision);COUNT(calculation_sequence);COUNT(vehicle_sequence);
 COUNT(world_sequence);COUNT(plan_revision);COUNT(phase_revision);NUM(segment_index);NUM(cusp_index);
 COUNT(completed_guest_us);COUNT(valid_until_pi_us);STR(driver_session);COUNT(driver_action_seq);COUNT(driver_state_seq);
 if(third){rc|=xm_put(out,n,&pos,",\"clock_probe_sequence\":\"");rc|=xm_put_uint(out,n,&pos,probe);
  rc|=xm_put(out,n,&pos,"\",\"next_direction\":");
  rc|=xm_put(out,n,&pos,next<0?"-1":next>0?"1":"0");}
 rc|=xm_put(out,n,&pos,"}");
#undef STR
#undef NUM
#undef COUNT
 return rc?-ENOSPC:pos>XM_COMMAND2_BYTES?-EMSGSIZE:(int)pos;
}
int xm_drive_command2_json(char *out,size_t n,const struct xm_drive_command2 *v)
{
 return command_json(out,n,v,0,0,false);
}
int xm_drive_command3_json(char *out,size_t n,const struct xm_drive_command3 *v)
{
 return v?command_json(out,n,&v->command,v->clock_probe_sequence,v->next_direction,true):-EINVAL;
}
size_t xm_message_max_bytes(const char *type,struct xm_span payload)
{
 /* Profile declarations only select a bounded transport ceiling. The actual
  * receiving parser still validates the full payload before output admission. */
 struct xm_json j;
 if(type&&payload.p&&payload.len<=XM_COMMAND2_BYTES&&!xm_json_parse(&j,payload.p,payload.len)&&xm_json_is_object(&j,0)){
  struct xm_span p=xm_json_span(&j,xm_json_get(&j,0,"profile"));
  if((!strcmp(type,"drive.command")&&(xm_span_eq(p,"drive-command-2")||xm_span_eq(p,"drive-command-3")))||
     (!strcmp(type,"drive.input")&&(xm_span_eq(p,"manual-input-2")||xm_span_eq(p,"manual-input-3"))))return XM_MAX_PROFILE_BYTES;
 }
 return xm_policy_max_bytes(xm_policy_for(type));
}
