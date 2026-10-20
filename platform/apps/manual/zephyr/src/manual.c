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
	if (in->input_payload.p != NULL && in->input_age_ms <= cfg->input_timeout_ms &&
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
