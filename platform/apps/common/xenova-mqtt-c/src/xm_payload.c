/* 주행 payload 인코드·디코드 (계약 5.4 drive.command·vehicle.control·perception.result, 6 perception.event, 6.1 app.heartbeat, 7 drive.input) */
#include <errno.h>
#include <string.h>

#include "xm_internal.h"

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
	return 0;
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
