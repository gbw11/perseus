/* 주행 payload 인코드·디코드 (계약 5.4 drive.command·vehicle.control, 6.1 app.heartbeat, 7 drive.input) */
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

int xm_vehicle_control_parse(struct xm_span payload, struct xm_vehicle_control *out)
{
	static struct xm_json j;
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
	static struct xm_json j;

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
