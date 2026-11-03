/*
 * 자율주행 앱 로직 (OS 무관). 최신 perception.result 로 목표 속도·조향을 정한다 (계약 §5.4).
 *  - 조향: pure pursuit. 인지 정의(heading_err = atan((c(L) − c(0)) / L), lane_offset = −c(0))를 거꾸로 써서
 *    카메라 L 앞 차로 중심 점 y = −lane_offset + L·tan(heading_err) 을 얻고, 후륜 축에서 그 점 (camera_ahead + L, y)
 *    을 지나는 원호 곡률 k = 2y / (x² + y²) 로 steer = atan(wheelbase·k) − k_offset·lane_offset, ±max_steer 로 자름.
 *    차가 원호 위에 있으면 그 원호의 조향을 내므로 급코너에서 안쪽으로 파고들지 않는다 (S15P31E110-360).
 *    lookahead 0.8 m 원호는 곡률 1.25 (반경 0.8 m) 넘게 못 내서 그 근처 코너 진입에서 밀리는 만큼을 offset 항이 당긴다
 *    (lane_offset + = 차가 왼쪽 → 오른쪽(−)으로, heading_err + = 차선이 왼쪽으로 돈다 → 왼쪽(+)으로)
 *  - 속도: stop 이면 0. 아니면 차 앞 통로(|y| ≤ corridor_half + w/2) 안 가장 가까운 장애물 거리로
 *    slow_m → stop_m 사이에서 cruise → 0 으로 줄이고, 조향이 클수록 조금 줄인다
 *  - perception.result 가 perception_timeout_ms 보다 오래됐거나 형식이 틀리면 정지 (시나리오 F6·D5).
 *    명령은 계속 내므로 게이트웨이가 수동으로 넘기지 않는다
 *  - generation 은 vehicle/control 값, 못 받았으면 0. heartbeat state 는 수동 앱과 같은 규칙 (계약 §6.1)
 */
#ifndef XENOVA_AUTONOMOUS_H_
#define XENOVA_AUTONOMOUS_H_

#include <stdbool.h>
#include <stdint.h>

#include "xenova/mqtt/xm.h"

#define AUTO_APP_ID                "autonomous"
#define AUTO_PERIOD_MS             20  /* 계약 5.3: 50 Hz */
#define AUTO_HEARTBEAT_MS          1000
#define AUTO_PERCEPTION_TIMEOUT_MS 300 /* 15 Hz 결과를 4번 넘게 놓치면 끊긴 것 */

struct auto_cfg {
	float cruise_mps;     /* 장애물 없을 때 속도 */
	float max_steer_rad;
	float wheelbase_m;    /* 전·후륜 축 거리 */
	float camera_ahead_m; /* 후륜 축 → 카메라(인지 좌표 원점) 전방 거리 */
	float lookahead_m;    /* 인지가 heading_err 를 재는 거리 (인지 대역 LANE_LOOKAHEAD_M 과 같아야 한다) */
	float k_offset;       /* pure pursuit 에 더하는 차로 중심 복귀 (rad/m) */
	float corridor_half_m; /* 차 앞 통로 반폭 (차폭/2 + 여유, 인지 대역 StopZone 과 같다) */
	float slow_m;         /* 이 거리부터 감속 */
	float stop_m;         /* 이 거리 안이면 정지 (인지 대역 stop 거리와 같다) */
	float turn_slowdown;  /* 조향이 최대일 때 줄이는 비율 */
	int64_t perception_timeout_ms;
};

/* 왜 그 명령을 냈는지 — 화면(PV 콘솔 상태 줄)·perception.event 용 */
enum auto_reason {
	AUTO_REASON_NO_PERCEPTION = 0, /* 인지 결과 없음·끊김 → 정지 */
	AUTO_REASON_CRUISE,            /* 차선 따라 주행 */
	AUTO_REASON_SLOWING,           /* 앞 장애물로 감속 */
	AUTO_REASON_OBSTACLE_STOP,     /* 정지 영역 안 장애물 → 정지 */
};

struct auto_inputs {
	struct xm_span perception_payload; /* perception.result, 없으면 p == NULL */
	int64_t perception_age_ms;
	struct xm_span control_payload;    /* vehicle.control */
};

struct auto_command {
	uint32_t generation;
	float v_mps;
	float steer_rad;
};

struct auto_status {
	const char *state;       /* app.heartbeat: idle / active / standby */
	enum auto_reason reason;
	float nearest_m;         /* 통로 안 가장 가까운 장애물 x, 없으면 −1 */
	float lane_offset_m, heading_err_rad;
};

void auto_cfg_default(struct auto_cfg *cfg);
/* pure pursuit 조향 (자르기 전). 시험·Python 대역(simulator/tools/closed_loop.py)과 같은 식 */
float auto_pursuit_steer(const struct auto_cfg *cfg, float lane_offset_m, float heading_err_rad);
/* 인지 결과 하나 → 목표 (v, steer). reason·nearest_m 을 채운다 */
void auto_target(const struct auto_cfg *cfg, const struct xm_perception_result *p, float *v_mps, float *steer_rad,
		 enum auto_reason *reason, float *nearest_m);
/* 한 주기 계산 */
void auto_step(const struct auto_cfg *cfg, const struct auto_inputs *in, struct auto_command *out,
	       struct auto_status *status);
const char *auto_reason_name(enum auto_reason reason);
/* XenStore "vm" = "/vm/<uuid>" 에서 vm_id (수동 앱과 같은 규칙, 설계 §6 D5) */
int auto_vm_id_from_xenstore(const char *path, char out[XM_UUID_LEN + 1]);

#endif /* XENOVA_AUTONOMOUS_H_ */
