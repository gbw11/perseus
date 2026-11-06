/* 주행 payload 인코드·디코드 (계약 5.4 drive.command·vehicle.control·perception.result, 6 perception.event, 6.1 app.heartbeat, 7 drive.input) */
#include <errno.h>
#include <string.h>

#include "xm_internal.h"

static int parse_object(struct xm_json *j, struct xm_span payload);

static int command_text(const struct xm_json *j,int obj,const char *key,char *out,size_t n,bool optional)
{
	int token=xm_json_get(j,obj,key);
	if(token<0)return optional?0:-EINVAL;
	if(!xm_json_is_string(j,token)||xm_json_span(j,token).len==0||xm_span_copy(out,n,xm_json_span(j,token))<0)return -EINVAL;
	return 0;
}

int xm_auto_delay_command_parse(struct xm_span payload,struct xm_auto_delay_command *out)
{
	struct xm_json j;int64_t delay,duration;char expires[40],action[16];
	if(!out||parse_object(&j,payload))return -EINVAL;
	memset(out,0,sizeof(*out));
	/* command_id, expected_boot_id, expires_at, action, parameters plus the
	 * optional operation_id are the only accepted fields. */
	if(j.tok[0].size<5||j.tok[0].size>6||
	   command_text(&j,0,"command_id",out->command_id,sizeof(out->command_id),false)||
	   command_text(&j,0,"operation_id",out->operation_id,sizeof(out->operation_id),true)||
	   command_text(&j,0,"expected_boot_id",out->expected_boot_id,sizeof(out->expected_boot_id),false)||
	   command_text(&j,0,"expires_at",expires,sizeof(expires),false)||
	   command_text(&j,0,"action",action,sizeof(action),false)||strcmp(action,"auto_delay"))return -EINVAL;
	int parameters=xm_json_get(&j,0,"parameters");
	if(!xm_json_is_object(&j,parameters)||j.tok[parameters].size!=2||
	   xm_json_int(&j,xm_json_get(&j,parameters,"delay_ms"),&delay)||
	   xm_json_int(&j,xm_json_get(&j,parameters,"duration_s"),&duration)||
	   delay<1||delay>100||duration<1||duration>120)return -ERANGE;
	out->delay_ms=(uint32_t)delay;out->duration_s=(uint32_t)duration;return 0;
}

int xm_auto_delay_result_json(char *out,size_t n,const struct xm_auto_delay_command *c,
			      const char *status,uint64_t duration_ms,const char *error)
{
	if(!out||!c||!status)return -EINVAL;
	size_t pos=0;int rc=0;
	rc|=xm_put(out,n,&pos,"{\"command_id\":");rc|=xm_put_json_string(out,n,&pos,c->command_id);
	rc|=xm_put(out,n,&pos,",\"operation_id\":");rc|=c->operation_id[0]?xm_put_json_string(out,n,&pos,c->operation_id):xm_put(out,n,&pos,"null");
	rc|=xm_put(out,n,&pos,",\"status\":");rc|=xm_put_json_string(out,n,&pos,status);
	rc|=xm_put(out,n,&pos,",\"started_at\":null,\"finished_at\":null,\"duration_ms\":");
	rc|=duration_ms?xm_put_uint(out,n,&pos,duration_ms):xm_put(out,n,&pos,"null");
	rc|=xm_put(out,n,&pos,",\"result\":{\"delay_ms\":");rc|=xm_put_uint(out,n,&pos,c->delay_ms);
	rc|=xm_put(out,n,&pos,",\"duration_s\":");rc|=xm_put_uint(out,n,&pos,c->duration_s);
	rc|=xm_put(out,n,&pos,"},\"error\":");
	if(error){rc|=xm_put(out,n,&pos,"{\"code\":");rc|=xm_put_json_string(out,n,&pos,error);rc|=xm_put(out,n,&pos,",\"message\":");rc|=xm_put_json_string(out,n,&pos,error);rc|=xm_put(out,n,&pos,"}");}
	else rc|=xm_put(out,n,&pos,"null");
	rc|=xm_put(out,n,&pos,"}");return rc?-ENOSPC:(int)pos;
}

int xm_resource_stress_command_parse(struct xm_span payload,struct xm_resource_stress_command *out)
{
	struct xm_json j;int64_t workers=0,load=0,memory=0,duration;char expires[40],action[24];
	if(!out||parse_object(&j,payload))return -EINVAL;
	memset(out,0,sizeof(*out));
	if(j.tok[0].size<5||j.tok[0].size>6||
	   command_text(&j,0,"command_id",out->command_id,sizeof(out->command_id),false)||
	   command_text(&j,0,"operation_id",out->operation_id,sizeof(out->operation_id),true)||
	   command_text(&j,0,"expected_boot_id",out->expected_boot_id,sizeof(out->expected_boot_id),false)||
	   command_text(&j,0,"expires_at",expires,sizeof(expires),false)||
	   command_text(&j,0,"action",action,sizeof(action),false))return -EINVAL;
	if(strcmp(action,"cpu_stress")&&strcmp(action,"memory_stress"))return -ENOTSUP;
	int parameters=xm_json_get(&j,0,"parameters");
	if(!xm_json_is_object(&j,parameters))return -ERANGE;
	if(!strcmp(action,"cpu_stress")){
		out->action=XM_CPU_STRESS;
		if(j.tok[parameters].size!=3||
		   xm_json_int(&j,xm_json_get(&j,parameters,"workers"),&workers)||
		   xm_json_int(&j,xm_json_get(&j,parameters,"load_percent"),&load)||
		   xm_json_int(&j,xm_json_get(&j,parameters,"duration_s"),&duration)||
		   workers<1||workers>4||load<10||load>100||duration<1||duration>120)return -ERANGE;
		out->workers=(uint32_t)workers;out->load_percent=(uint32_t)load;
	}else{
		out->action=XM_MEMORY_STRESS;
		if(j.tok[parameters].size!=2||
		   xm_json_int(&j,xm_json_get(&j,parameters,"memory_mib"),&memory)||
		   xm_json_int(&j,xm_json_get(&j,parameters,"duration_s"),&duration)||
		   memory<1||memory>8||duration<1||duration>120)return -ERANGE;
		out->memory_mib=(uint32_t)memory;
	}
	out->duration_s=(uint32_t)duration;return 0;
}

int xm_resource_stress_result_json(char *out,size_t n,const struct xm_resource_stress_command *c,
				   const char *status,uint64_t duration_ms,const char *error)
{
	if(!out||!c||!status)return -EINVAL;
	size_t pos=0;int rc=0;
	rc|=xm_put(out,n,&pos,"{\"command_id\":");rc|=xm_put_json_string(out,n,&pos,c->command_id);
	rc|=xm_put(out,n,&pos,",\"operation_id\":");rc|=c->operation_id[0]?xm_put_json_string(out,n,&pos,c->operation_id):xm_put(out,n,&pos,"null");
	rc|=xm_put(out,n,&pos,",\"status\":");rc|=xm_put_json_string(out,n,&pos,status);
	rc|=xm_put(out,n,&pos,",\"started_at\":null,\"finished_at\":null,\"duration_ms\":");
	rc|=duration_ms?xm_put_uint(out,n,&pos,duration_ms):xm_put(out,n,&pos,"null");rc|=xm_put(out,n,&pos,",\"result\":{");
	if(c->action==XM_CPU_STRESS){rc|=xm_put(out,n,&pos,"\"workers\":");rc|=xm_put_uint(out,n,&pos,c->workers);rc|=xm_put(out,n,&pos,",\"load_percent\":");rc|=xm_put_uint(out,n,&pos,c->load_percent);}
	else{rc|=xm_put(out,n,&pos,"\"memory_mib\":");rc|=xm_put_uint(out,n,&pos,c->memory_mib);}
	rc|=xm_put(out,n,&pos,",\"duration_s\":");rc|=xm_put_uint(out,n,&pos,c->duration_s);
	rc|=xm_put(out,n,&pos,"},\"error\":");
	if(error){rc|=xm_put(out,n,&pos,"{\"code\":");rc|=xm_put_json_string(out,n,&pos,error);rc|=xm_put(out,n,&pos,",\"message\":");rc|=xm_put_json_string(out,n,&pos,error);rc|=xm_put(out,n,&pos,"}");}else rc|=xm_put(out,n,&pos,"null");
	rc|=xm_put(out,n,&pos,"}");return rc?-ENOSPC:(int)pos;
}

int xm_drive_command_json(char *out, size_t n, uint32_t generation, float v_mps, float steer_rad)
{
	size_t pos = 0;
	int rc = 0;

	rc |= xm_put(out, n, &pos, "{\"generation\":");
	rc |= xm_put_uint(out, n, &pos, generation);
	rc |= xm_put(out, n, &pos, ",\"v_mps\":");
	rc |= xm_put_fixed(out, n, &pos, (double)v_mps, 4);
	rc |= xm_put(out, n, &pos, ",\"steer_rad\":");
	rc |= xm_put_fixed(out, n, &pos, (double)steer_rad, 4);
	rc |= xm_put(out, n, &pos, "}");
	return rc ? -ENOSPC : (int)pos;
}

int xm_app_heartbeat_json(char *out, size_t n, const char *state, int64_t uptime_ms)
{
	size_t pos = 0;
	int rc = 0;

	if (state == NULL || uptime_ms < 0) {
		return -EINVAL;
	}
	rc |= xm_put(out, n, &pos, "{\"state\":");
	rc |= xm_put_json_string(out, n, &pos, state);
	rc |= xm_put(out, n, &pos, ",\"uptime_s\":");
	rc |= xm_put_fixed(out, n, &pos, (double)uptime_ms / 1000.0, 3);
	rc |= xm_put(out, n, &pos, "}");
	return rc ? -ENOSPC : (int)pos;
}

static int parse_object(struct xm_json *j, struct xm_span payload)
{
	if (payload.p == NULL || xm_json_parse(j, payload.p, payload.len) != 0 || !xm_json_is_object(j, 0)) {
		return -EINVAL;
	}
	return 0;
}

int xm_app_readiness_json(char *out,size_t n,uint32_t generation,uint64_t progress,
                         const char *readiness,const char *reason)
{
	if (!readiness || (strcmp(readiness,"READY") && strcmp(readiness,"PREPARING") && strcmp(readiness,"NOT_READY"))) return -EINVAL;
	if ((!strcmp(readiness,"READY")) != (reason==NULL)) return -EINVAL;
	if (!strcmp(readiness,"READY") && (!generation || !progress)) return -EINVAL;
	static const char *const reasons[]={"CONTROL_UNAPPLIED","INPUT_UNAVAILABLE","INPUT_STALE","INPUT_INVALID",
		"STATE_PREPARING","CALCULATION_FAILED","OUTPUT_INVALID"};
	if(reason){bool known=false;for(size_t i=0;i<sizeof(reasons)/sizeof(reasons[0]);i++)known|=!strcmp(reason,reasons[i]);if(!known)return -EINVAL;}
	size_t pos=0;int rc=0;
	rc|=xm_put(out,n,&pos,"{\"applied_generation\":");rc|=xm_put_uint(out,n,&pos,generation);
	rc|=xm_put(out,n,&pos,",\"progress_seq\":\"");rc|=xm_put_uint(out,n,&pos,progress);
	rc|=xm_put(out,n,&pos,"\",\"readiness\":");rc|=xm_put_json_string(out,n,&pos,readiness);
	rc|=xm_put(out,n,&pos,",\"reason\":");
	rc|=reason?xm_put_json_string(out,n,&pos,reason):xm_put(out,n,&pos,"null");
	rc|=xm_put(out,n,&pos,"}");return rc?-ENOSPC:(int)pos;
}

int xm_app_lifecycle_json(char *out,size_t n,uint32_t generation,uint64_t check,
 uint64_t progress,const char *phase,const char *state,const char *reason)
{
 if(!check||!phase||!state)return -EINVAL;
 bool waiting=!strcmp(phase,"WAITING"),executing=!strcmp(phase,"EXECUTING");
 if(!waiting&&!executing&&strcmp(phase,"ACTIVATING")&&strcmp(phase,"STOPPING")&&strcmp(phase,"RESIDUAL"))return -EINVAL;
 if(strcmp(state,"QUALIFIED")&&strcmp(state,"READY")&&strcmp(state,"NOT_READY"))return -EINVAL;
 if((!strcmp(state,"NOT_READY"))!=(reason!=NULL))return -EINVAL;
 if(!strcmp(state,"QUALIFIED")&&(!waiting||!generation))return -EINVAL;
 if(!strcmp(state,"READY")&&(!executing||!generation||!progress))return -EINVAL;
 if(reason){
  static const char *const reasons[]={"CONTROL_UNAPPLIED","INPUT_UNAVAILABLE","INPUT_STALE","INPUT_INVALID",
   "STATE_PREPARING","CALCULATION_FAILED","OUTPUT_INVALID","APP_STOPPING","RESOURCE_RETAINED",
   "BUSY","MAP_UNAVAILABLE","WORLD_UNAVAILABLE","POSE_UNAVAILABLE","PATH_UNAVAILABLE","PATH_INVALIDATED",
   "OBSERVATION_EXPIRED","CONTEXT_CHANGED","STEERING_UNAVAILABLE","CLOCK_ERROR","SEARCH_NOT_FOUND",
   "NODE_BUDGET","WORK_BUDGET","DEADLINE","CANCELLED","NUMERIC_ERROR","COLLISION","PATH_LIMIT",
   "STATE_REJECTED","RESOURCE_LIMIT","STATE_FRAME_CHANGED","STATE_CONFIGURATION_CHANGED","STATE_REFERENCE_CHANGED"};
  bool known=false;for(size_t i=0;i<sizeof(reasons)/sizeof(reasons[0]);i++)known|=!strcmp(reason,reasons[i]);
  if(!known)return -EINVAL;
 }
 size_t pos=0;int rc=0;
 rc|=xm_put(out,n,&pos,"{\"profile\":\"warm-drive-1\",\"applied_generation\":");rc|=xm_put_uint(out,n,&pos,generation);
 rc|=xm_put(out,n,&pos,",\"check_seq\":\"");rc|=xm_put_uint(out,n,&pos,check);
 rc|=xm_put(out,n,&pos,"\",\"progress_seq\":\"");rc|=xm_put_uint(out,n,&pos,progress);
 rc|=xm_put(out,n,&pos,"\",\"phase\":");rc|=xm_put_json_string(out,n,&pos,phase);
 rc|=xm_put(out,n,&pos,",\"readiness\":");rc|=xm_put_json_string(out,n,&pos,state);
 rc|=xm_put(out,n,&pos,",\"reason\":");
 rc|=reason?xm_put_json_string(out,n,&pos,reason):xm_put(out,n,&pos,"null");
 rc|=xm_put(out,n,&pos,"}");return rc?-ENOSPC:(int)pos;
}

static bool canonical_uuid(const char *value)
{
	if (strlen(value) != XM_UUID_LEN) { return false; }
	for (size_t i = 0; i < XM_UUID_LEN; ++i) {
		if (i == 8 || i == 13 || i == 18 || i == 23) {
			if (value[i] != '-') { return false; }
		} else if (!((value[i] >= '0' && value[i] <= '9') || (value[i] >= 'a' && value[i] <= 'f'))) {
			return false;
		}
	}
	return true;
}

int xm_vehicle_control_parse(struct xm_span payload, struct xm_vehicle_control *out)
{
	/* App owners and the control coordinator may parse concurrently. */
	struct xm_json j;
	int64_t generation;

	memset(out, 0, sizeof(*out));
	if (parse_object(&j, payload) != 0) {
		return -EINVAL;
	}
	/* generation 은 0 이상 정수. bool·실수·음수는 무효 (Python 앱의 _generation 과 같다) */
	if (xm_json_int(&j, xm_json_get(&j, 0, "generation"), &generation) != 0 || generation < 0 ||
	    generation > UINT32_MAX) {
		return -EINVAL;
	}
	out->generation = (uint32_t)generation;
	int mode = xm_json_get(&j, 0, "mode");
	int active = xm_json_get(&j, 0, "active_app");

	if (xm_json_is_string(&j, mode)) {
		xm_span_copy(out->mode, sizeof(out->mode), xm_json_span(&j, mode));
	}
	if (xm_json_is_string(&j, active)) {
		xm_span_copy(out->active_app, sizeof(out->active_app), xm_json_span(&j, active));
	}
	int vm = xm_json_get(&j, 0, "active_vm_id");
	if (vm >= 0 && !xm_json_is_null(&j, vm)) {
		if (!xm_json_is_string(&j, vm) ||
		    xm_span_copy(out->active_vm_id, sizeof(out->active_vm_id), xm_json_span(&j, vm)) < 0 ||
		    !canonical_uuid(out->active_vm_id)) { return -EINVAL; }
	}
 int policy=xm_json_get(&j,0,"manual_policy"),session=xm_json_get(&j,0,"manual_session");
 if(policy>=0||session>=0){
  if(!xm_json_is_string(&j,policy)||!xm_json_is_string(&j,session)||
     xm_span_copy(out->manual_policy,sizeof(out->manual_policy),xm_json_span(&j,policy))<0||
     xm_span_copy(out->manual_session,sizeof(out->manual_session),xm_json_span(&j,session))<0||
     !canonical_uuid(out->manual_session)||!strcmp(out->manual_session,"00000000-0000-0000-0000-000000000000")||
     (strcmp(out->manual_policy,"CONTINUE_SESSION")&&strcmp(out->manual_policy,"NEUTRAL_THEN_ACTION"))||
     (strcmp(out->mode,"MANUAL")&&strcmp(out->mode,"AUTO_READY")))return -EINVAL;
 }
	return 0;
}

static int source_counter(const struct xm_json *j,const char *name,uint64_t *out)
{
 int token=xm_json_get(j,0,name);if(!xm_json_is_string(j,token))return -EINVAL;
 struct xm_span s=xm_json_span(j,token);uint64_t value=0;
 if(!s.len||s.len>20||(s.len>1&&s.p[0]=='0'))return -EINVAL;
 for(unsigned i=0;i<s.len;i++){
  if(s.p[i]<'0'||s.p[i]>'9'||value>(UINT64_MAX-(s.p[i]-'0'))/10)return -EINVAL;
  value=value*10+(s.p[i]-'0');
 }
 *out=value;return 0;
}
int xm_manual_sample_parse(struct xm_span payload,struct xm_manual_sample *out)
{
 struct xm_json j;int64_t generation,age;memset(out,0,sizeof(*out));
 if(parse_object(&j,payload))return -EINVAL;
 if(xm_json_get(&j,0,"profile")<0){
  static const char *const metadata[]={"session_id","sample_seq","action_seq","generation","valid","age_ms"};
  for(unsigned i=0;i<6;i++)if(xm_json_get(&j,0,metadata[i])>=0)return -EINVAL;
  return -ENOMSG;
 }
 if(j.tok[0].size!=8||
    !xm_json_is_string(&j,xm_json_get(&j,0,"profile"))||
    !xm_span_eq(xm_json_span(&j,xm_json_get(&j,0,"profile")),"manual-session-1")||
    source_counter(&j,"sample_seq",&out->sample)||!out->sample||source_counter(&j,"action_seq",&out->action)||
    xm_json_int(&j,xm_json_get(&j,0,"generation"),&generation)||generation<1||generation>UINT32_MAX||
    xm_json_int(&j,xm_json_get(&j,0,"age_ms"),&age)||age<0||age>INT32_MAX)return -EINVAL;
 int session=xm_json_get(&j,0,"session_id");
 if(!xm_json_is_string(&j,session)||xm_span_copy(out->session,sizeof(out->session),xm_json_span(&j,session))<0||
    !canonical_uuid(out->session)||!strcmp(out->session,"00000000-0000-0000-0000-000000000000"))return -EINVAL;
 if(!xm_json_is_bool(&j,xm_json_get(&j,0,"valid"),&out->valid)||xm_drive_input_parse(payload,&out->keys))return -EINVAL;
 int array=xm_json_get(&j,0,"keys"),token=array+1;uint32_t seen=0;
 if(j.tok[array].size>5)return -EINVAL;
 static const char *const names[]={"up","down","left","right","brake"};
 for(int i=0;i<j.tok[array].size;i++){
  uint32_t bit=0;
  if(!xm_json_is_string(&j,token))return -EINVAL;
  for(unsigned k=0;k<5;k++)if(xm_span_eq(xm_json_span(&j,token),names[k]))bit=1u<<k;
  if(!bit||(seen&bit))return -EINVAL;
  seen|=bit;token=xm_json_skip(&j,token);
 }
 out->generation=generation;out->age_ms=age;return 0;
}

int xm_drive_input_parse(struct xm_span payload, uint32_t *keys)
{
	static const struct {
		const char *name;
		uint32_t bit;
	} KEYS[] = {
		{"up", XM_KEY_UP}, {"down", XM_KEY_DOWN}, {"left", XM_KEY_LEFT},
		{"right", XM_KEY_RIGHT}, {"brake", XM_KEY_BRAKE},
	};
	struct xm_json j;

	*keys = 0;
	if (parse_object(&j, payload) != 0) {
		return -EINVAL;
	}
	int arr = xm_json_get(&j, 0, "keys");

	if (arr < 0 || j.tok[arr].type != JSMN_ARRAY) {
		return -EINVAL;
	}
	int i = arr + 1;

	for (int e = 0; e < j.tok[arr].size && i < j.count; e++) {
		if (xm_json_is_string(&j, i)) {
			struct xm_span s = xm_json_span(&j, i);

			for (size_t k = 0; k < sizeof(KEYS) / sizeof(KEYS[0]); k++) {
				if (xm_span_eq(s, KEYS[k].name)) {
					*keys |= KEYS[k].bit;
				}
			}
		}
		i = xm_json_skip(&j, i); /* 숫자·객체 등 모르는 원소는 무시 */
	}
	return 0;
}

static int finite_float(const struct xm_json *j, int i, float *out)
{
	double v;

	/* isfinite 대신 범위로 (minimal libc). 1e30 넘는 값은 주행 값으로 의미가 없다 */
	if (xm_json_number(j, i, &v) != 0 || !(v > -1e30 && v < 1e30)) {
		return -EINVAL;
	}
	*out = (float)v;
	return 0;
}

int xm_perception_result_parse(struct xm_span payload, struct xm_perception_result *out)
{
	struct xm_json j;

	memset(out, 0, sizeof(*out));
	if (parse_object(&j, payload) != 0 ||
	    finite_float(&j, xm_json_get(&j, 0, "lane_offset_m"), &out->lane_offset_m) != 0 ||
	    finite_float(&j, xm_json_get(&j, 0, "heading_err_rad"), &out->heading_err_rad) != 0 ||
	    !xm_json_is_bool(&j, xm_json_get(&j, 0, "stop"), &out->stop)) {
		memset(out, 0, sizeof(*out));
		return -EINVAL;
	}
	int arr = xm_json_get(&j, 0, "obstacles");

	if (arr < 0 || j.tok[arr].type != JSMN_ARRAY) {
		memset(out, 0, sizeof(*out));
		return -EINVAL;
	}
	int i = arr + 1;

	for (int e = 0; e < j.tok[arr].size && i < j.count; e++) {
		struct xm_obstacle o;

		if (out->n_obstacles < XM_PERCEPTION_MAX_OBSTACLES && xm_json_is_object(&j, i) &&
		    finite_float(&j, xm_json_get(&j, i, "x"), &o.x) == 0 &&
		    finite_float(&j, xm_json_get(&j, i, "y"), &o.y) == 0 &&
		    finite_float(&j, xm_json_get(&j, i, "w"), &o.w) == 0 && o.w >= 0.0f) {
			out->obstacles[out->n_obstacles++] = o;
		}
		i = xm_json_skip(&j, i);
	}
	return 0;
}

int xm_perception_event_json(char *out, size_t n, const char *kind, const char *detail)
{
	size_t pos = 0;
	int rc = 0;

	if (kind == NULL || detail == NULL) {
		return -EINVAL;
	}
	rc |= xm_put(out, n, &pos, "{\"kind\":");
	rc |= xm_put_json_string(out, n, &pos, kind);
	rc |= xm_put(out, n, &pos, ",\"detail\":");
	rc |= xm_put_json_string(out, n, &pos, detail);
	rc |= xm_put(out, n, &pos, "}");
	return rc ? -ENOSPC : (int)pos;
}

/* drive-state-1 uses the existing bounded JSON parser/writer. Exact object
 * cardinality plus all required keys rejects duplicate/unknown/missing keys. */
static int state_object(const struct xm_json *j,int obj,const char *const *keys,size_t n)
{
 if(!xm_json_is_object(j,obj)||j->tok[obj].size!=(int)n)return -EINVAL;
 for(size_t i=0;i<n;i++)if(xm_json_get(j,obj,keys[i])<0)return -EINVAL;
 return 0;
}
static int state_string(const struct xm_json *j,int obj,const char *key,char *out,size_t n)
{
 int token=xm_json_get(j,obj,key);
 return !xm_json_is_string(j,token)||xm_span_copy(out,n,xm_json_span(j,token))<0?-EINVAL:0;
}
static int state_counter(const struct xm_json *j,int obj,const char *key,uint64_t *out)
{
 int token=xm_json_get(j,obj,key);if(!xm_json_is_string(j,token))return -EINVAL;
 struct xm_span s=xm_json_span(j,token);uint64_t value=0;
 if(!s.len||s.len>20||(s.len>1&&s.p[0]=='0'))return -EINVAL;
 for(size_t i=0;i<s.len;i++){
  if(s.p[i]<'0'||s.p[i]>'9'||value>(UINT64_MAX-(s.p[i]-'0'))/10)return -EINVAL;
  value=value*10+(s.p[i]-'0');
 }
 *out=value;return 0;
}
static int state_integer(const struct xm_json *j,const char *key,uint64_t *out)
{
 int64_t value;
 if(xm_json_int(j,xm_json_get(j,0,key),&value)||value<0)return -EINVAL;
 *out=(uint64_t)value;return 0;
}
static bool state_uuid(const char *s)
{
 for(size_t i=0;i<XM_UUID_LEN;i++)if(!s[i])return false;
 return !s[XM_UUID_LEN]&&canonical_uuid(s);
}
static int state_context_parse(const struct xm_json *j,struct xm_state_context *out)
{
 int obj=xm_json_get(j,0,"context");
 static const char *const keys[]={"frame","configuration","maneuver","odom_epoch","road_revision","route_revision"};
 if(state_object(j,obj,keys,6)||state_counter(j,obj,"frame",&out->frame)||
    state_counter(j,obj,"configuration",&out->configuration)||state_counter(j,obj,"maneuver",&out->maneuver)||
    state_counter(j,obj,"road_revision",&out->road_revision)||state_counter(j,obj,"route_revision",&out->route_revision)||
    state_string(j,obj,"odom_epoch",out->odom_epoch,sizeof(out->odom_epoch))||!state_uuid(out->odom_epoch))return -EINVAL;
 return 0;
}
static bool state_labels(const struct xm_state_snapshot *v)
{
 if(!memchr(v->kind,0,sizeof(v->kind))||!memchr(v->side,0,sizeof(v->side))||
    !memchr(v->phase,0,sizeof(v->phase))||!memchr(v->merge_segment,0,sizeof(v->merge_segment)))return false;
 if((strcmp(v->kind,"FOLLOW")&&strcmp(v->kind,"AVOID"))||
    (strcmp(v->side,"NONE")&&strcmp(v->side,"LEFT")&&strcmp(v->side,"RIGHT"))||
    (strcmp(v->phase,"FOLLOW")&&strcmp(v->phase,"PASS")&&strcmp(v->phase,"REJOIN")))return false;
 bool follow=!strcmp(v->kind,"FOLLOW");
 if(follow!=(!strcmp(v->phase,"FOLLOW"))||follow!=(!strcmp(v->side,"NONE"))||!v->merge_segment[0])return false;
 for(const char *s=v->merge_segment;*s;s++)if(!((*s>='a'&&*s<='z')||(*s>='A'&&*s<='Z')||
     (*s>='0'&&*s<='9')||*s=='_'||*s=='.'||*s==':'||*s=='-'))return false;
 return true;
}
int xm_state_snapshot_parse(struct xm_span payload,struct xm_state_snapshot *out)
{
 struct xm_json j;int64_t generation;memset(out,0,sizeof(*out));
 static const char *const keys[]={"profile","execution_id","intent_revision","lifecycle_generation",
  "source_generation","state_revision","context","maneuver"};
 if(payload.len>4096||parse_object(&j,payload)||state_object(&j,0,keys,8)||
    !xm_span_eq(xm_json_span(&j,xm_json_get(&j,0,"profile")),"drive-state-1")||
    state_string(&j,0,"execution_id",out->execution,sizeof(out->execution))||!state_uuid(out->execution)||
    state_integer(&j,"intent_revision",&out->intent_revision)||state_integer(&j,"lifecycle_generation",&out->lifecycle_generation)||
    xm_json_int(&j,xm_json_get(&j,0,"source_generation"),&generation)||generation<1||generation>UINT32_MAX||
    state_counter(&j,0,"state_revision",&out->state_revision)||!out->state_revision||state_context_parse(&j,&out->context))return -EINVAL;
 out->source_generation=(uint32_t)generation;
 int obj=xm_json_get(&j,0,"maneuver");
 static const char *const fields[]={"kind","side","phase","merge_segment"};
 if(state_object(&j,obj,fields,4)||state_string(&j,obj,"kind",out->kind,sizeof(out->kind))||
    state_string(&j,obj,"side",out->side,sizeof(out->side))||state_string(&j,obj,"phase",out->phase,sizeof(out->phase))||
    state_string(&j,obj,"merge_segment",out->merge_segment,sizeof(out->merge_segment))||!state_labels(out))return -EINVAL;
 return 0;
}
static int state_qnumber(char *out,size_t n,size_t *pos,uint64_t value)
{
 int rc=xm_put(out,n,pos,"\"");rc|=xm_put_uint(out,n,pos,value);rc|=xm_put(out,n,pos,"\"");return rc;
}
static int state_context_json(char *out,size_t n,size_t *pos,const struct xm_state_context *c)
{
 int rc=xm_put(out,n,pos,",\"context\":{\"frame\":");rc|=state_qnumber(out,n,pos,c->frame);
 rc|=xm_put(out,n,pos,",\"configuration\":");rc|=state_qnumber(out,n,pos,c->configuration);
 rc|=xm_put(out,n,pos,",\"maneuver\":");rc|=state_qnumber(out,n,pos,c->maneuver);
 rc|=xm_put(out,n,pos,",\"odom_epoch\":");rc|=xm_put_json_string(out,n,pos,c->odom_epoch);
 rc|=xm_put(out,n,pos,",\"road_revision\":");rc|=state_qnumber(out,n,pos,c->road_revision);
 rc|=xm_put(out,n,pos,",\"route_revision\":");rc|=state_qnumber(out,n,pos,c->route_revision);
 rc|=xm_put(out,n,pos,"}");return rc;
}
static int state_prefix(char *out,size_t n,size_t *pos,const char *execution,uint64_t intent,uint64_t lifecycle,uint64_t revision)
{
 int rc=xm_put(out,n,pos,"{\"profile\":\"drive-state-1\",\"execution_id\":");
 rc|=xm_put_json_string(out,n,pos,execution);
 rc|=xm_put(out,n,pos,",\"intent_revision\":");rc|=xm_put_uint(out,n,pos,intent);
 rc|=xm_put(out,n,pos,",\"lifecycle_generation\":");rc|=xm_put_uint(out,n,pos,lifecycle);
 rc|=xm_put(out,n,pos,",\"state_revision\":");rc|=state_qnumber(out,n,pos,revision);return rc;
}
int xm_state_snapshot_json(char *out,size_t n,const struct xm_state_snapshot *v)
{
 if(!v||!v->source_generation||!v->state_revision||!state_uuid(v->execution)||!state_uuid(v->context.odom_epoch)||
    v->intent_revision>INT64_MAX||v->lifecycle_generation>INT64_MAX||!state_labels(v))return -EINVAL;
 size_t pos=0;int rc=state_prefix(out,n,&pos,v->execution,v->intent_revision,v->lifecycle_generation,v->state_revision);
 rc|=xm_put(out,n,&pos,",\"source_generation\":");rc|=xm_put_uint(out,n,&pos,v->source_generation);
 rc|=state_context_json(out,n,&pos,&v->context);
 rc|=xm_put(out,n,&pos,",\"maneuver\":{\"kind\":");rc|=xm_put_json_string(out,n,&pos,v->kind);
 rc|=xm_put(out,n,&pos,",\"side\":");rc|=xm_put_json_string(out,n,&pos,v->side);
 rc|=xm_put(out,n,&pos,",\"phase\":");rc|=xm_put_json_string(out,n,&pos,v->phase);
 rc|=xm_put(out,n,&pos,",\"merge_segment\":");rc|=xm_put_json_string(out,n,&pos,v->merge_segment);
 rc|=xm_put(out,n,&pos,"}}");return rc?-ENOSPC:(int)pos;
}
int xm_state_application_parse(struct xm_span payload,struct xm_state_application *out)
{
 struct xm_json j;memset(out,0,sizeof(*out));
 static const char *const keys[]={"profile","execution_id","intent_revision","lifecycle_generation",
  "copy_message_id","state_revision","context","origin","phase"};
 if(payload.len>4096||parse_object(&j,payload)||state_object(&j,0,keys,9)||
    !xm_span_eq(xm_json_span(&j,xm_json_get(&j,0,"profile")),"drive-state-1")||
    state_string(&j,0,"execution_id",out->execution,sizeof(out->execution))||!state_uuid(out->execution)||
    state_integer(&j,"intent_revision",&out->intent_revision)||state_integer(&j,"lifecycle_generation",&out->lifecycle_generation)||
    state_counter(&j,0,"state_revision",&out->state_revision)||!out->state_revision||state_context_parse(&j,&out->context))return -EINVAL;
 struct xm_span origin=xm_json_span(&j,xm_json_get(&j,0,"origin")),phase=xm_json_span(&j,xm_json_get(&j,0,"phase"));
 if(!xm_span_eq(origin,"handoff")&&!xm_span_eq(origin,"rebuilt"))return -EINVAL;
 if(!xm_span_eq(phase,"APPLIED")&&!xm_span_eq(phase,"REJECTED"))return -EINVAL;
 out->rebuilt=xm_span_eq(origin,"rebuilt");out->applied=xm_span_eq(phase,"APPLIED");
 int copy=xm_json_get(&j,0,"copy_message_id");
 if(out->rebuilt){if(!xm_json_is_null(&j,copy))return -EINVAL;}
 else if(state_string(&j,0,"copy_message_id",out->copy_message_id,sizeof(out->copy_message_id))||!state_uuid(out->copy_message_id))return -EINVAL;
 return 0;
}
int xm_state_application_json(char *out,size_t n,const struct xm_state_application *v)
{
 if(!v||!v->state_revision||!state_uuid(v->execution)||!state_uuid(v->context.odom_epoch)||
    v->intent_revision>INT64_MAX||v->lifecycle_generation>INT64_MAX||
    (v->rebuilt?v->copy_message_id[0]!=0:!state_uuid(v->copy_message_id)))return -EINVAL;
 size_t pos=0;int rc=state_prefix(out,n,&pos,v->execution,v->intent_revision,v->lifecycle_generation,v->state_revision);
 rc|=xm_put(out,n,&pos,",\"copy_message_id\":");rc|=v->rebuilt?xm_put(out,n,&pos,"null"):xm_put_json_string(out,n,&pos,v->copy_message_id);
 rc|=state_context_json(out,n,&pos,&v->context);
 rc|=xm_put(out,n,&pos,",\"origin\":");rc|=xm_put_json_string(out,n,&pos,v->rebuilt?"rebuilt":"handoff");
 rc|=xm_put(out,n,&pos,",\"phase\":");rc|=xm_put_json_string(out,n,&pos,v->applied?"APPLIED":"REJECTED");
 rc|=xm_put(out,n,&pos,"}");return rc?-ENOSPC:(int)pos;
}
