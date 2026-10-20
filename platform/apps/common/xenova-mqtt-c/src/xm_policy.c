/* type 별 QoS·retain·크기 — Python xenova_mqtt/policy.py 표와 같다 (tests/vectors/policy.json 으로 대조) */
#include <string.h>

#include "xenova/mqtt/xm.h"

static const struct xm_policy POLICIES[] = {
	/* 3장 에이전트 */
	{"agent.heartbeat", 1, false, false},
	{"agent.inventory", 1, true, false},
	{"agent.metrics", 0, false, false},
	{"agent.command", 1, false, false},
	{"agent.command_result", 1, false, false},
	/* 5장 주행 제어 */
	{"drive.command", 0, false, true},
	{"perception.result", 0, false, false},
	{"vehicle.control", 1, true, true},
	{"vehicle.status", 0, false, true},
	/* 6장 앱 이벤트 */
	{"perception.event", 1, false, false},
	{"message.posted", 1, false, false},
	{"drive.resume.requested", 1, false, false},
	{"drive.mode.changed", 1, false, false},
	{"drive.fault.detected", 1, false, false},
	{"app.heartbeat", 0, false, false},
	/* 7장 수동 입력 */
	{"drive.input", 0, false, false},
	/* 8장 지연 측정 */
	{"probe.ping", 1, false, false},
	{"probe.pong", 1, false, false},
};

/* 계약 표에 없는 type. 유실보다 중복이 낫다고 보고 QoS 1 */
static const struct xm_policy DEFAULT_POLICY = {NULL, 1, false, false};

const struct xm_policy *xm_policy_for(const char *type)
{
	for (size_t i = 0; type != NULL && i < sizeof(POLICIES) / sizeof(POLICIES[0]); i++) {
		if (strcmp(type, POLICIES[i].type) == 0) {
			return &POLICIES[i];
		}
	}
	return &DEFAULT_POLICY;
}

size_t xm_policy_max_bytes(const struct xm_policy *p)
{
	return p->driving ? XM_MAX_DRIVING_BYTES : XM_MAX_BYTES;
}
