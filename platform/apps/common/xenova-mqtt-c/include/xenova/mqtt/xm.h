/*
 * xenova_mqtt C판 — MQTT 계약 v1 (docs/contracts/mqtt-contract-v1.md) 코어.
 * Python xenova_mqtt(platform/services/vm-agent/mqtt-client)와 같은 토픽·정책·봉투·검사·seq 규칙.
 * 설계: docs/design/mqtt-c-client.md. 동적 메모리를 쓰지 않는다. 함수는 재진입 가능하지만 객체는 한 스레드가 소유한다.
 *
 * 반환값: 0 이상 성공(문자열 함수는 길이), 음수 errno (-EINVAL, -ENOSPC, -EMSGSIZE).
 */
#ifndef XENOVA_MQTT_XM_H_
#define XENOVA_MQTT_XM_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define XM_PREFIX            "xenova/v1"
#define XM_SCHEMA_VERSION    "1.0"
#define XM_MAX_BYTES         (64u * 1024u)
#define XM_MAX_DRIVING_BYTES 1024u
#define XM_ID_MAX            64   /* vm_id·app_id·source_id 최대 길이 + NUL */
#define XM_TOPIC_MAX         128
#define XM_UUID_LEN          36

#ifndef XM_JSON_MAX_TOKENS
#define XM_JSON_MAX_TOKENS   96   /* 봉투 + payload 토큰. perception.result 장애물 몇 개까지 */
#endif
#ifndef XM_SEQ_SLOTS
#define XM_SEQ_SLOTS         8
#endif

/* ---- 토픽 (계약 3·5·6·7·8장) ---------------------------------------- */

int xm_topic_vm(char *out, size_t n, const char *vm_id, const char *kind); /* kind: heartbeat … command-result */
int xm_topic_app_events(char *out, size_t n, const char *app_id);
int xm_topic_manual_input(char *out, size_t n);
int xm_topic_vehicle_control(char *out, size_t n);
int xm_topic_vehicle_status(char *out, size_t n);
int xm_topic_probe_request(char *out, size_t n, const char *target_vm_id);
int xm_topic_probe_reply(char *out, size_t n, const char *source_vm_id);

struct xm_topic_info {
	char vm_id[XM_ID_MAX];  /* 빈 문자열이면 없음 */
	char app_id[XM_ID_MAX];
};

/* vms/{vm_id}/{kind} 와 apps/{app_id}/events 에서 id 를 꺼낸다. 계약 밖 토픽이면 둘 다 빈 문자열 */
void xm_topic_parse(const char *topic, struct xm_topic_info *info);
bool xm_topic_matches(const char *filter, const char *topic);

/* ---- 정책 (type 별 QoS·retain·크기) ---------------------------------- */

struct xm_policy {
	const char *type;   /* DEFAULT 는 NULL */
	uint8_t qos;
	bool retain;
	bool driving;       /* 1 KB 제한, latest-only */
};

const struct xm_policy *xm_policy_for(const char *type); /* 표에 없으면 DEFAULT (QoS 1) */
size_t xm_policy_max_bytes(const struct xm_policy *p);

/* ---- 봉투 (계약 2장) ------------------------------------------------ */

struct xm_identity {
	const char *vm_id;      /* vm_id 와 source_id 중 정확히 하나 */
	const char *source_id;
	const char *app_id;     /* NULL 이면 토픽에서 */
	char boot_id[XM_UUID_LEN + 1];
	char instance_id[XM_UUID_LEN + 1]; /* agent_instance_id */
};

int xm_identity_init(struct xm_identity *id, const char *vm_id, const char *source_id, const char *app_id,
		     const char *boot_id, const char *instance_id);

/* 16 bytes 난수로 UUID v4 문자열(36자 + NUL)을 만든다 */
void xm_uuid4_format(char out[XM_UUID_LEN + 1], const uint8_t random16[16]);

/*
 * 봉투를 JSON 으로 쓴다. 필드 순서는 Python envelope.build 와 같다.
 * app_id 가 NULL 이면 identity.app_id, 그것도 NULL 이면 토픽(apps/{app_id}/events)에서 정한다.
 * payload_json 은 JSON 객체 문자열 그대로 붙인다. 정책의 크기 한도를 넘으면 -EMSGSIZE.
 */
int xm_envelope_encode(char *out, size_t n, const struct xm_identity *id, const char *topic, const char *type,
		       uint32_t seq, const char *message_id, const char *sent_at, const char *payload_json);

/* 받은 JSON 의 원문 조각. NUL 종단이 아니다 */
struct xm_span {
	const char *p;
	uint16_t len;
};

struct xm_envelope {
	struct xm_span message_id, vm_id, source_id, app_id, boot_id, instance_id, type, sent_at;
	struct xm_span payload; /* {...} 원문 */
	int64_t seq;            /* 없으면 -1 (agent.command) */
};

/*
 * Python envelope.validate 와 같은 순서·같은 오류 코드로 검사한다.
 * 실패하면 음수를 돌려주고 *code 에 "PAYLOAD_INVALID" "SCHEMA_VERSION_UNSUPPORTED" "VM_ID_MISMATCH"
 * "APP_ID_MISMATCH" 중 하나를 둔다. json 은 NUL 종단이 아니어도 된다.
 */
int xm_envelope_decode(const char *topic, const char *json, size_t len, struct xm_envelope *env, const char **code);

bool xm_span_eq(struct xm_span s, const char *text);
int xm_span_copy(char *out, size_t n, struct xm_span s);

/* ---- seq 중복·역전 처리 --------------------------------------------- */

struct xm_seq_slot {
	char topic[XM_TOPIC_MAX];
	char publisher[XM_ID_MAX];
	char run[2 * XM_UUID_LEN + 2]; /* boot_id + '/' + agent_instance_id */
	int64_t last;
	uint32_t used;                 /* LRU */
};

struct xm_seq_tracker {
	struct xm_seq_slot slots[XM_SEQ_SLOTS];
	uint32_t clock;
};

void xm_seq_init(struct xm_seq_tracker *t);
/* (토픽, 발행자) 별 마지막 seq 이하를 버린다. boot_id·agent_instance_id 가 바뀌면 기준을 새로 잡는다 */
bool xm_seq_accept(struct xm_seq_tracker *t, const char *topic, const struct xm_envelope *env);

/* ---- 주행 payload (계약 5.4·6.1·7) ----------------------------------- */

int xm_drive_command_json(char *out, size_t n, uint32_t generation, float v_mps, float steer_rad);
int xm_app_heartbeat_json(char *out, size_t n, const char *state, int64_t uptime_ms);

struct xm_vehicle_control {
	uint32_t generation;
	char mode[16];
	char active_app[XM_ID_MAX]; /* null 이면 빈 문자열 */
};
int xm_vehicle_control_parse(struct xm_span payload, struct xm_vehicle_control *out);

enum xm_key {
	XM_KEY_UP = 1u << 0,
	XM_KEY_DOWN = 1u << 1,
	XM_KEY_LEFT = 1u << 2,
	XM_KEY_RIGHT = 1u << 3,
	XM_KEY_BRAKE = 1u << 4,
};
/* keys 배열을 비트마스크로. 모르는 키는 무시. keys 가 배열이 아니면 -EINVAL */
int xm_drive_input_parse(struct xm_span payload, uint32_t *keys);

#ifdef __cplusplus
}
#endif

#endif /* XENOVA_MQTT_XM_H_ */
