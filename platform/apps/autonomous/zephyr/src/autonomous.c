/* 자율주행 앱 로직 — 규칙은 autonomous.h, 시험은 tests/test_autonomous.c */
#include <errno.h>
#include <string.h>

#include "autonomous.h"

void auto_cfg_default(struct auto_cfg *cfg)
{
	/* 실차 측정 전 값. 1/10 RC카 실내 시연 기준, 시나리오 전환 중 이동 거리 목표(0.5 m/s 상한)에 맞춘다 */
	cfg->cruise_mps = 0.5f;
	cfg->max_steer_rad = 0.4f;
	cfg->k_offset = 1.5f;
	cfg->k_heading = 1.0f;
	cfg->corridor_half_m = 0.15f; /* 인지 대역 StopZone: 차폭 0.20/2 + 여유 0.05 */
	cfg->slow_m = 1.6f;
	cfg->stop_m = 0.8f;           /* 인지 대역 StopZone.distance_m */
	cfg->turn_slowdown = 0.4f;
	cfg->perception_timeout_ms = AUTO_PERCEPTION_TIMEOUT_MS;
}

/* xenvm 은 Zephyr minimal libc 라 math.h 함수가 없다 */
static float absf(float v)
{
	return v < 0.0f ? -v : v;
}

static float clampf(float v, float lo, float hi)
{
	return v < lo ? lo : (v > hi ? hi : v);
}

void auto_target(const struct auto_cfg *cfg, const struct xm_perception_result *p, float *v_mps, float *steer_rad,
		 enum auto_reason *reason, float *nearest_m)
{
	float nearest = -1.0f;

	for (int i = 0; i < p->n_obstacles; i++) {
		const struct xm_obstacle *o = &p->obstacles[i];

		if (o->x > 0.0f && absf(o->y) <= cfg->corridor_half_m + o->w / 2.0f && (nearest < 0.0f || o->x < nearest)) {
			nearest = o->x;
		}
	}
	*nearest_m = nearest;

	float steer = cfg->k_heading * p->heading_err_rad - cfg->k_offset * p->lane_offset_m;

	*steer_rad = clampf(steer, -cfg->max_steer_rad, cfg->max_steer_rad);

	/* 인지가 정지를 요구하거나 정지 거리 안이면 선다. 인지 대역과 거리 기준이 달라도 더 이른 쪽을 따른다 */
	if (p->stop || (nearest >= 0.0f && nearest <= cfg->stop_m)) {
		*v_mps = 0.0f;
		*reason = AUTO_REASON_OBSTACLE_STOP;
		return;
	}
	float v = cfg->cruise_mps * (1.0f - cfg->turn_slowdown * absf(*steer_rad) / cfg->max_steer_rad);

	*reason = AUTO_REASON_CRUISE;
	if (nearest >= 0.0f && nearest < cfg->slow_m) {
		v *= (nearest - cfg->stop_m) / (cfg->slow_m - cfg->stop_m);
		*reason = AUTO_REASON_SLOWING;
	}
	*v_mps = v;
}

void auto_step(const struct auto_cfg *cfg, const struct auto_inputs *in, struct auto_command *out,
	       struct auto_status *status)
{
	struct xm_vehicle_control control;
	struct xm_perception_result p;

	out->v_mps = 0.0f;
	out->steer_rad = 0.0f;
	status->reason = AUTO_REASON_NO_PERCEPTION;
	status->nearest_m = -1.0f;
	status->lane_offset_m = 0.0f;
	status->heading_err_rad = 0.0f;
	if (in->perception_payload.p != NULL && in->perception_age_ms <= cfg->perception_timeout_ms &&
	    xm_perception_result_parse(in->perception_payload, &p) == 0) {
		auto_target(cfg, &p, &out->v_mps, &out->steer_rad, &status->reason, &status->nearest_m);
		status->lane_offset_m = p.lane_offset_m;
		status->heading_err_rad = p.heading_err_rad;
	}

	if (in->control_payload.p != NULL && xm_vehicle_control_parse(in->control_payload, &control) == 0) {
		out->generation = control.generation;
		status->state = strcmp(control.active_app, AUTO_APP_ID) == 0 ? "active" : "standby";
	} else {
		out->generation = 0; /* 게이트웨이가 세대가 다르면 버리므로 차는 움직이지 않는다 */
		status->state = "idle";
	}
}

const char *auto_reason_name(enum auto_reason reason)
{
	switch (reason) {
	case AUTO_REASON_CRUISE:
		return "cruise";
	case AUTO_REASON_SLOWING:
		return "slowing";
	case AUTO_REASON_OBSTACLE_STOP:
		return "obstacle_stop";
	default:
		return "no_perception";
	}
}

/* 수동 앱 manual_vm_id_from_xenstore 와 같은 규칙 (두 앱이 같은 시험 표를 쓴다) */
int auto_vm_id_from_xenstore(const char *path, char out[XM_UUID_LEN + 1])
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
		return -EINVAL; /* nil UUID 는 Domain-0 몫 */
	}
	return 0;
}
