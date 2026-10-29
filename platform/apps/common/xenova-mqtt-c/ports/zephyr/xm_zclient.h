/*
 * Zephyr net/mqtt 위의 계약 v1 클라이언트 (Python XenovaClient 대응, 설계 §2·§5).
 * clean session, MQTT 3.1.1, 연결될 때마다 재구독, type 별 QoS·retain, 수신 검사 → seq → latest 보관.
 * 한 스레드가 소유한다: init·subscribe·poll·publish·latest 를 같은 스레드에서 부른다.
 */
#ifndef XENOVA_MQTT_XM_ZCLIENT_H_
#define XENOVA_MQTT_XM_ZCLIENT_H_

#include <zephyr/kernel.h>
#include <zephyr/net/mqtt.h>
#include <zephyr/net/socket.h>

#include "xenova/mqtt/xm.h"
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
#include <xenova/mqtt_socket.h>
#endif

struct xm_zlatest {
	char topic[XM_TOPIC_MAX];
	char data[CONFIG_XENOVA_MQTT_RX_BUF];
	size_t len;
	int64_t rx_ms; /* 받은 쪽 단조 시계 (계약 §1) */
};

struct xm_zsub {
	char filter[XM_TOPIC_MAX];
	uint8_t qos;
};

struct xm_zpub {
	char topic[XM_TOPIC_MAX];
	uint32_t seq;
};

struct xm_zclient {
	struct mqtt_client mqtt;
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
	struct xt_socket socket_io;
#endif
	int payload_rc;
	struct sockaddr_storage broker;
	struct mqtt_utf8 client_id, username, password;
	char client_id_buf[XM_ID_MAX + 16];
	uint8_t rx_buf[CONFIG_XENOVA_MQTT_RX_BUF];
	uint8_t tx_buf[CONFIG_XENOVA_MQTT_TX_BUF];
	uint8_t payload[CONFIG_XENOVA_MQTT_RX_BUF];
	char out[XM_MAX_DRIVING_BYTES + 256];

	struct xm_identity id;
	struct xm_seq_tracker seq;
	struct xm_zsub subs[CONFIG_XENOVA_MQTT_MAX_SUBS];
	struct xm_zlatest latest[CONFIG_XENOVA_MQTT_LATEST_SLOTS];
	struct xm_zpub pubs[CONFIG_XENOVA_MQTT_PUB_TOPICS];
	int nsubs;
	/* Optional synchronous accepted-input guard, owned by the polling thread.
	 * Runs after envelope/sequence/subscription checks, before latest replacement.
	 * Nonzero rejects only this cache update. Must not block or perform I/O. */
	int (*accept_input)(void *, const char *, const struct xm_envelope *, int64_t);
	void *accept_context;

	bool socket_open;
	bool connected;
	int64_t retry_at_ms;
	int32_t backoff_ms;
	uint32_t message_counter;
	uint16_t packet_id;

	/* 통계 */
	uint32_t published, dropped, received;
	uint32_t connection_attempts;
	int last_io_rc;
	/* Optional local send ceiling, never a new wire timestamp. */
	int64_t publish_deadline_ms;
};

struct xm_zconfig {
	const char *host;      /* IPv4 문자열 */
	uint16_t port;
	const char *username;  /* NULL 이면 익명 */
	const char *password;
	uint16_t keepalive_s;
	/* Optional trusted execution/session UUIDs, both or neither. Defaults keep
	 * legacy random sessions; no v1 envelope fields or time semantics change. */
	const char *boot_id, *instance_id;
};

/* vm_id 또는 source_id 정확히 하나. boot_id·agent_instance_id 는 sys_rand 로 만든다 (설계 §6 D4) */
int xm_zclient_init(struct xm_zclient *c, const char *vm_id, const char *source_id, const char *app_id,
		    const struct xm_zconfig *cfg);
/* handler 없이 latest-only 로 보관한다. 연결 중이면 바로 SUBSCRIBE, 아니면 연결 때 */
int xm_zclient_subscribe(struct xm_zclient *c, const char *filter, uint8_t qos);
/* 연결·재연결(1→10초)·keepalive·수신 처리. timeout_ms 동안 소켓을 기다린다 */
int xm_zclient_poll(struct xm_zclient *c, int32_t timeout_ms);
/* 봉투를 씌워 발행. 끊겨 있으면 -ENOTCONN (QoS 0 는 버린다, Python 과 같다) */
int xm_zclient_publish(struct xm_zclient *c, const char *topic, const char *type, const char *payload_json);
int xm_zclient_publish_until(struct xm_zclient *, const char *topic, const char *type,
			    const char *payload_json, int64_t deadline_ms);
/* Single transport owner; immutable per-message app identity. The connection
 * identity never changes. Caller must validate the root-issued app binding. */
int xm_zclient_publish_as_until(struct xm_zclient *,const char *topic,const char *type,
                               const char *payload,const char *instance_id,int64_t deadline_ms);
/* topic 의 마지막 메시지와 나이(ms). 없으면 -ENOENT. env 의 span 은 다음 poll 전까지 유효 */
/* Single owner only. Abort pending I/O; no offline flush or publish retry. */
void xm_zclient_close(struct xm_zclient *c);
int xm_zclient_latest(struct xm_zclient *c, const char *topic, struct xm_envelope *env, int64_t *age_ms);

#endif /* XENOVA_MQTT_XM_ZCLIENT_H_ */
