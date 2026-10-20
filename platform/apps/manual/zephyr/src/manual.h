/*
 * 수동주행 앱 로직 (OS 무관). Python 참조 구현(platform/apps/manual/xenova_manual)과 같은 동작:
 *  - 키 → (v_mps, steer_rad): up 전진, down 후진, left +, right −, 반대 키 동시면 그 축 0, brake 면 속도 0 (계약 §7)
 *  - drive.input 이 input_timeout_ms 보다 오래됐거나 keys 가 배열이 아니면 정지
 *  - generation 은 vehicle/control 값, 못 받았으면 0
 *  - app.heartbeat state: control 없음 idle, active_app == manual 이면 active, 아니면 standby (계약 §6.1)
 */
#ifndef XENOVA_MANUAL_H_
#define XENOVA_MANUAL_H_

#include <stdbool.h>
#include <stdint.h>

#include "xenova/mqtt/xm.h"

#define MANUAL_APP_ID           "manual"
#define MANUAL_PERIOD_MS        20   /* 계약 5.3: 50 Hz */
#define MANUAL_HEARTBEAT_MS     1000 /* 계약 6.1 */
#define MANUAL_INPUT_TIMEOUT_MS 200  /* 계약 7: 4번 놓치면 손을 뗀 것 */

struct manual_cfg {
	float max_speed_mps;
	float max_reverse_mps;
	float max_steer_rad;
	int64_t input_timeout_ms;
};

/* 한 주기 입력: 최신 메시지의 payload 와 나이. 없으면 payload.p == NULL */
struct manual_inputs {
	struct xm_span input_payload;   /* drive.input */
	int64_t input_age_ms;
	struct xm_span control_payload; /* vehicle.control */
};

struct manual_command {
	uint32_t generation;
	float v_mps;
	float steer_rad;
};

void manual_cfg_default(struct manual_cfg *cfg);
/*
 * Xen 이 자기 도메인의 XenStore "vm" 에 적어 둔 "/vm/<uuid>" 에서 vm_id 를 꺼낸다 (설계 §6 D5).
 * xen.cfg 의 uuid 가 플랫폼 vm_id 라 에이전트의 /etc/xenova/instance-id 와 같은 값이다. 소문자 표준형으로 낸다.
 */
int manual_vm_id_from_xenstore(const char *path, char out[XM_UUID_LEN + 1]);
void manual_target(const struct manual_cfg *cfg, uint32_t keys, float *v_mps, float *steer_rad);
/* 한 주기 계산. *state 에 heartbeat state 문자열 */
void manual_step(const struct manual_cfg *cfg, const struct manual_inputs *in, struct manual_command *out,
		 const char **state);

#endif /* XENOVA_MANUAL_H_ */
