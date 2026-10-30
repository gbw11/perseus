/* Zephyr net/mqtt 포트 — 연결·재연결·재구독·발행·수신 검사·latest (Python client.py 대응) */
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include <zephyr/logging/log.h>
#include <zephyr/random/random.h>
#include <zephyr/sys/util.h>

#include "xm_zclient.h"

LOG_MODULE_REGISTER(xenova_mqtt, LOG_LEVEL_INF);

#define BACKOFF_MIN_MS   1000  /* Python 클라이언트와 같은 1→10초 */
#define BACKOFF_MAX_MS   10000
#define CONNACK_WAIT_MS  5000

static struct xm_zclient *owner(struct mqtt_client *m)
{
	return CONTAINER_OF(m, struct xm_zclient, mqtt);
}

static void io_budget(struct xm_zclient *c)
{
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
	c->socket_io.deadline_ms = k_uptime_get() + CONFIG_XENOVA_MQTT_IO_MS;
	if (c->publish_deadline_ms) { c->socket_io.deadline_ms = MIN(c->socket_io.deadline_ms, c->publish_deadline_ms); }
#else
	ARG_UNUSED(c);
#endif
}

static void utf8(struct mqtt_utf8 *u, const char *s)
{
	u->utf8 = (const uint8_t *)s;
	u->size = s ? strlen(s) : 0;
}

/* 벽시계가 없으므로 1970-01-01 + 업타임 (설계 §6 D2). 계약상 시각 비교로 판정하지 않는다 */
static void sent_at(char out[32])
{
	int64_t ms = k_uptime_get();
	int64_t s = ms / 1000;

	snprintf(out, 32, "1970-01-%02dT%02d:%02d:%02d.%03dZ", (int)MIN(1 + s / 86400, 28), (int)(s / 3600 % 24),
		 (int)(s / 60 % 60), (int)(s % 60), (int)(ms % 1000));
}

static void schedule_retry(struct xm_zclient *c)
{
	c->connected = false;
	c->socket_open = false;
	c->retry_at_ms = k_uptime_get() + c->backoff_ms;
	c->backoff_ms = MIN(c->backoff_ms * 2, BACKOFF_MAX_MS);
}

static int send_subscribe(struct xm_zclient *c, int first, int count)
{
	struct mqtt_topic topics[CONFIG_XENOVA_MQTT_MAX_SUBS];
	struct mqtt_subscription_list list = {.list = topics, .list_count = (uint16_t)count};

	for (int i = 0; i < count; i++) {
		utf8(&topics[i].topic, c->subs[first + i].filter);
		topics[i].qos = c->subs[first + i].qos;
	}
	list.message_id = ++c->packet_id ? c->packet_id : ++c->packet_id;
	io_budget(c);
	return count ? mqtt_subscribe(&c->mqtt, &list) : 0;
}

static struct xm_zlatest *latest_slot(struct xm_zclient *c, const char *topic, bool create)
{
	struct xm_zlatest *oldest = &c->latest[0];

	for (int i = 0; i < CONFIG_XENOVA_MQTT_LATEST_SLOTS; i++) {
		if (c->latest[i].topic[0] && strcmp(c->latest[i].topic, topic) == 0) {
			return &c->latest[i];
		}
		if (c->latest[i].rx_ms < oldest->rx_ms) {
			oldest = &c->latest[i];
		}
	}
	if (!create) {
		return NULL;
	}
	strcpy(oldest->topic, topic);
	return oldest;
}

static void on_publish(struct xm_zclient *c, const struct mqtt_publish_param *p)
{
	char topic[XM_TOPIC_MAX];
	size_t len = p->message.payload.len;
	size_t tlen = p->message.topic.topic.size;
	bool fits = len <= sizeof(c->payload) && tlen < sizeof(topic);

	/* 큰 메시지도 소켓에서 끝까지 읽어 내야 다음 패킷을 읽을 수 있다 */
	if (fits) {
		if ((c->payload_rc = mqtt_readall_publish_payload(&c->mqtt, c->payload, len)) < 0) {
			return;
		}
	} else {
		for (size_t left = len; left > 0;) {
			int n = mqtt_read_publish_payload_blocking(&c->mqtt, c->payload, MIN(left, sizeof(c->payload)));

			if (n <= 0) {
				c->payload_rc = n ? n : -EIO;
				return;
			}
			left -= (size_t)n;
		}
	}
	if (p->message.topic.qos == MQTT_QOS_1_AT_LEAST_ONCE) {
		struct mqtt_puback_param ack = {.message_id = p->message_id};

		mqtt_publish_qos1_ack(&c->mqtt, &ack);
	}
	c->received++;
	if (!fits) {
		c->dropped++;
		LOG_WRN("버림: PAYLOAD_TOO_LARGE %u bytes", (unsigned int)len);
		return;
	}
	memcpy(topic, p->message.topic.topic.utf8, tlen);
	topic[tlen] = '\0';

	struct xm_envelope env;
	const char *code;

	if (xm_envelope_decode(topic, (const char *)c->payload, len, &env, &code) != 0) {
		c->dropped++;
		LOG_WRN("버림 %s: %s", topic, code);
		return;
	}
	char type[48];

	if (xm_span_copy(type, sizeof(type), env.type) < 0 || len > xm_policy_max_bytes(xm_policy_for(type))) {
		c->dropped++;
		LOG_WRN("버림 %s: PAYLOAD_TOO_LARGE %s", topic, type);
		return;
	}
	if (!xm_seq_accept(&c->seq, topic, &env)) {
		return; /* 중복·역전 */
	}
	for (int i = 0; i < c->nsubs; i++) {
		if (xm_topic_matches(c->subs[i].filter, topic)) {
			struct xm_zlatest *slot = latest_slot(c, topic, true);

			memcpy(slot->data, c->payload, len);
			slot->len = len;
			slot->rx_ms = k_uptime_get();
			break;
		}
	}
}

static void on_event(struct mqtt_client *m, const struct mqtt_evt *evt)
{
	struct xm_zclient *c = owner(m);

	switch (evt->type) {
	case MQTT_EVT_CONNACK:
		if (evt->result != 0) {
			c->last_io_rc = -EACCES;
			LOG_WRN("MQTT 연결 거부: %d", evt->result);
			mqtt_abort(m);
			schedule_retry(c);
			return;
		}
		c->connected = true;
		c->backoff_ms = BACKOFF_MIN_MS;
		/* clean session 이라 브로커가 구독을 잊는다. 연결될 때마다 다시 건다 */
		send_subscribe(c, 0, c->nsubs);
		LOG_INF("MQTT 연결 (구독 %d개 복구)", c->nsubs);
		break;
	case MQTT_EVT_DISCONNECT:
		LOG_WRN("MQTT 연결 끊김, 재연결 대기");
		schedule_retry(c);
		break;
	case MQTT_EVT_PUBLISH:
		if (evt->result == 0) {
			on_publish(c, &evt->param.publish);
		}
		break;
	default:
		break;
	}
}

int xm_zclient_init(struct xm_zclient *c, const char *vm_id, const char *source_id, const char *app_id,
		    const struct xm_zconfig *cfg)
{
	uint8_t r[16];
	char boot[XM_UUID_LEN + 1], inst[XM_UUID_LEN + 1];
	struct sockaddr_in *sin = (struct sockaddr_in *)&c->broker;

	memset(c, 0, sizeof(*c));
	int rc;
	if (cfg->boot_id || cfg->instance_id) {
		if (!cfg->boot_id || !cfg->instance_id) { return -EINVAL; }
		rc = xm_identity_init(&c->id, vm_id, source_id, app_id, cfg->boot_id, cfg->instance_id);
	} else {
		sys_rand_get(r, sizeof(r));
		xm_uuid4_format(boot, r);
		sys_rand_get(r, sizeof(r));
		xm_uuid4_format(inst, r);
		rc = xm_identity_init(&c->id, vm_id, source_id, app_id, boot, inst);
	}

	if (rc) {
		return rc;
	}
	xm_seq_init(&c->seq);

	sin->sin_family = AF_INET;
	sin->sin_port = htons(cfg->port);
	if (zsock_inet_pton(AF_INET, cfg->host, &sin->sin_addr) != 1) {
		return -EINVAL;
	}

	mqtt_client_init(&c->mqtt);
	snprintf(c->client_id_buf, sizeof(c->client_id_buf), "%s-%.8s", vm_id ? vm_id : source_id, c->id.instance_id);
	utf8(&c->client_id, c->client_id_buf);
	c->mqtt.client_id = c->client_id;
	if (cfg->username && cfg->username[0]) {
		utf8(&c->username, cfg->username);
		c->mqtt.user_name = &c->username;
		if (cfg->password && cfg->password[0]) {
			utf8(&c->password, cfg->password);
			c->mqtt.password = &c->password;
		}
	}
	c->mqtt.broker = &c->broker;
	c->mqtt.evt_cb = on_event;
	c->mqtt.protocol_version = MQTT_VERSION_3_1_1;
	c->mqtt.clean_session = 1;
	c->mqtt.keepalive = cfg->keepalive_s ? cfg->keepalive_s : 30;
	c->mqtt.rx_buf = c->rx_buf;
	c->mqtt.rx_buf_size = sizeof(c->rx_buf);
	c->mqtt.tx_buf = c->tx_buf;
	c->mqtt.tx_buf_size = sizeof(c->tx_buf);
	#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
	c->socket_io.fd = -1;
	c->mqtt.transport.type = MQTT_TRANSPORT_CUSTOM;
	c->mqtt.transport.custom_transport_data = &c->socket_io;
#else
	c->mqtt.transport.type = MQTT_TRANSPORT_NON_SECURE;
#endif
	c->backoff_ms = BACKOFF_MIN_MS;
	return 0;
}

int xm_zclient_subscribe(struct xm_zclient *c, const char *filter, uint8_t qos)
{
	if (c->nsubs >= CONFIG_XENOVA_MQTT_MAX_SUBS || strlen(filter) >= XM_TOPIC_MAX) {
		return -ENOSPC;
	}
	strcpy(c->subs[c->nsubs].filter, filter);
	c->subs[c->nsubs].qos = qos;
	c->nsubs++;
	return c->connected ? send_subscribe(c, c->nsubs - 1, 1) : 0;
}

int xm_zclient_poll(struct xm_zclient *c, int32_t timeout_ms)
{
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
	if (c->socket_io.close_error) { return c->socket_io.close_error; }
#endif
	int64_t now = k_uptime_get();

	if (!c->socket_open) {
		if (now >= c->retry_at_ms) {
			++c->connection_attempts;
			io_budget(c);
			int rc = mqtt_connect(&c->mqtt); /* TCP 연결 + CONNECT. CONNACK 은 mqtt_input 에서 */

			if (rc == 0) {
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
				int fd = c->socket_io.fd;
#else
				int fd = c->mqtt.transport.tcp.sock;
#endif
				/* Every fresh v1 socket, including reconnects, disables Nagle
				 * before subscriptions or application messages can be sent. */
				const int enabled = 1;
				if (zsock_setsockopt(fd, NET_IPPROTO_TCP, ZSOCK_TCP_NODELAY,
						     &enabled, sizeof(enabled)) < 0) {
					rc = -errno;
				}
			}
			if (rc == 0) {
				c->socket_open = true;
				c->retry_at_ms = now + CONNACK_WAIT_MS;
			} else {
				c->last_io_rc = rc;
				LOG_DBG("MQTT 연결 실패 %d", rc);
				xm_zclient_close(c);
				schedule_retry(c);
			}
		}
		if (!c->socket_open) {
			k_msleep(MAX(timeout_ms, 0));
			return -ENOTCONN;
		}
	}
	if (!c->connected && k_uptime_get() >= c->retry_at_ms) {
		c->last_io_rc = -ETIMEDOUT;
		LOG_WRN("CONNACK 시간 초과");
		mqtt_abort(&c->mqtt);
		schedule_retry(c);
		return -ETIMEDOUT;
	}

	#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
	int fd = c->socket_io.fd;
#else
	int fd = c->mqtt.transport.tcp.sock;
#endif
	struct zsock_pollfd fds = {.fd = fd, .events = ZSOCK_POLLIN};
	int keep = mqtt_keepalive_time_left(&c->mqtt);
	int rc = zsock_poll(&fds, 1, MAX(MIN(timeout_ms, keep), 0));

	if (rc > 0 && (fds.revents & (ZSOCK_POLLIN | ZSOCK_POLLERR | ZSOCK_POLLHUP))) {
		io_budget(c);
		c->payload_rc = 0;
		rc = mqtt_input(&c->mqtt);
		if (c->payload_rc) { rc = c->payload_rc; }
		if (rc < 0 && c->socket_open) {
			c->last_io_rc = rc;
			LOG_WRN("MQTT 수신 오류 %d", rc);
			mqtt_abort(&c->mqtt);
			schedule_retry(c);
			return rc;
		}
	}
	if (c->socket_open) {
		io_budget(c);
		rc = mqtt_live(&c->mqtt);
		if (rc < 0 && rc != -EAGAIN) {
			mqtt_abort(&c->mqtt);
			schedule_retry(c);
			return rc;
		}
	}
	return 0;
}

int xm_zclient_publish(struct xm_zclient *c, const char *topic, const char *type, const char *payload_json)
{
	struct xm_zpub *pub = NULL;
	char message_id[12], at[32];

	if (!c->connected) {
		return -ENOTCONN;
	}
	for (int i = 0; i < CONFIG_XENOVA_MQTT_PUB_TOPICS && pub == NULL; i++) {
		if (c->pubs[i].topic[0] == '\0' || strcmp(c->pubs[i].topic, topic) == 0) {
			pub = &c->pubs[i];
		}
	}
	if (pub == NULL || strlen(topic) >= XM_TOPIC_MAX) {
		return -ENOSPC;
	}
	if (pub->topic[0] == '\0') {
		strcpy(pub->topic, topic);
	}
	/* message_id 는 seq 와 별개인 단조 카운터 (계약 §2 펌웨어 예외, 설계 §6 D3) */
	snprintf(message_id, sizeof(message_id), "%u", ++c->message_counter);
	sent_at(at);
	int len = xm_envelope_encode(c->out, sizeof(c->out), &c->id, topic, type, pub->seq + 1, message_id, at,
				     payload_json);

	if (len < 0) {
		return len;
	}
	const struct xm_policy *rule = xm_policy_for(type);
	struct mqtt_publish_param param = {0};

	utf8(&param.message.topic.topic, topic);
	param.message.topic.qos = rule->qos;
	param.message.payload.data = (uint8_t *)c->out;
	param.message.payload.len = (uint32_t)len;
	param.retain_flag = rule->retain;
	if (rule->qos > 0) {
		param.message_id = ++c->packet_id ? c->packet_id : ++c->packet_id;
	}
	io_budget(c);
	int rc = mqtt_publish(&c->mqtt, &param);

	if (rc == 0) {
		pub->seq++;
		c->published++;
	} else {
		/* A partial frame cannot be overwritten by the next candidate. Abort
		 * this byte stream; never retry this message on the next connection. */
		c->last_io_rc = rc;
		xm_zclient_close(c);
		schedule_retry(c);
	}
	return rc;
}

int xm_zclient_publish_until(struct xm_zclient *c,const char *topic,const char *type,
			    const char *json,int64_t end)
{
	if (end <= k_uptime_get()) { return -ETIMEDOUT; }
	c->publish_deadline_ms = end;
	int rc = xm_zclient_publish(c,topic,type,json);
	c->publish_deadline_ms = 0;
	return rc;
}

void xm_zclient_close(struct xm_zclient *c)
{
	io_budget(c);
	(void)mqtt_abort(&c->mqtt);
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
	/* Connect can fail before the MQTT library takes socket ownership. */
	(void)xt_socket_close(&c->socket_io);
#endif
	c->connected = false;
	c->socket_open = false;
}

int xm_zclient_latest(struct xm_zclient *c, const char *topic, struct xm_envelope *env, int64_t *age_ms)
{
	struct xm_zlatest *slot = latest_slot(c, topic, false);
	const char *code;

	if (slot == NULL || slot->len == 0) {
		return -ENOENT;
	}
	if (xm_envelope_decode(topic, slot->data, slot->len, env, &code) != 0) {
		return -EINVAL; /* 저장 전에 검사했으므로 일어나지 않는다 */
	}
	*age_ms = k_uptime_get() - slot->rx_ms;
	return 0;
}
