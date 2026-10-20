/*
 * Zephyr 수동주행 앱 (S15P31E110-155). 한 스레드가 MQTT 클라이언트를 소유하고 20 ms 마다:
 *   최신 drive.input·vehicle.control → manual_step → drive.command 발행, 1초마다 app.heartbeat.
 * 남는 시간에는 xm_zclient_poll 로 수신·keepalive·재연결을 처리한다.
 * 계약: docs/contracts/mqtt-contract-v1.md §5·§6.1·§7. Python 참조 구현: platform/apps/manual/xenova_manual
 */
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <string.h>

#include "manual.h"
#include "xm_zclient.h"

#if defined(CONFIG_XENOVA_XENSTORE)
#include <xenova/xenstore_zephyr.h>
#endif

LOG_MODULE_REGISTER(xenova_manual, LOG_LEVEL_INF);

static struct xm_zclient client;
static char vm_id[XM_UUID_LEN + 1] = CONFIG_XENOVA_MANUAL_VM_ID;

/* xenvm: XenStore 의 자기 도메인 "vm" (= "/vm/<uuid>") 에서 vm_id. 못 읽으면 Kconfig 값 (설계 §6 D5) */
static void resolve_vm_id(void)
{
#if defined(CONFIG_XENOVA_XENSTORE)
	static struct xs_zephyr store;
	char path[64];
	int rc = xs_zephyr_open(&store);

	if (rc == 0) {
		rc = xs_read_text(&store.link.client, 0, "vm", path, sizeof(path), 3000);
		if (rc == 0) {
			rc = manual_vm_id_from_xenstore(path, vm_id);
		}
		xs_zephyr_close(&store, 3000);
	}
	if (rc != 0) {
		LOG_WRN("XenStore vm_id 읽기 실패 %d, Kconfig 값 사용", rc);
		strcpy(vm_id, CONFIG_XENOVA_MANUAL_VM_ID);
	}
#endif
}

static void latest_payload(const char *topic, const char *type, struct xm_span *payload, int64_t *age_ms)
{
	struct xm_envelope env;

	payload->p = NULL;
	payload->len = 0;
	if (xm_zclient_latest(&client, topic, &env, age_ms) == 0 && xm_span_eq(env.type, type)) {
		*payload = env.payload;
	}
}

int main(void)
{
	char events[XM_TOPIC_MAX], control[XM_TOPIC_MAX], input[XM_TOPIC_MAX], json[160];
	const struct xm_zconfig zcfg = {
		.host = CONFIG_XENOVA_MANUAL_BROKER_HOST,
		.port = CONFIG_XENOVA_MANUAL_BROKER_PORT,
		.username = CONFIG_XENOVA_MANUAL_BROKER_USERNAME,
		.password = CONFIG_XENOVA_MANUAL_BROKER_PASSWORD,
		.keepalive_s = 30,
	};
	struct manual_cfg cfg;
	const char *last_state = NULL;

	manual_cfg_default(&cfg);
	xm_topic_app_events(events, sizeof(events), MANUAL_APP_ID);
	xm_topic_vehicle_control(control, sizeof(control));
	xm_topic_manual_input(input, sizeof(input));

	resolve_vm_id();
	int rc = xm_zclient_init(&client, vm_id, NULL, MANUAL_APP_ID, &zcfg);

	if (rc) {
		LOG_ERR("클라이언트 초기화 실패 %d", rc);
		return rc;
	}
	/* 핸들러 없이 latest-only. vehicle/control 은 retained 라 연결 직후 현재 세대가 온다 */
	xm_zclient_subscribe(&client, control, 1);
	xm_zclient_subscribe(&client, input, 0);
	LOG_INF("xenova-manual 시작 vm_id=%s broker=%s:%d rate=50Hz", vm_id, zcfg.host,
		zcfg.port);

	int64_t start = k_uptime_get();
	int64_t next_tick = start, next_beat = start;

	for (;;) {
		int64_t now = k_uptime_get();

		if (now >= next_tick) {
			struct manual_inputs in;
			struct manual_command cmd;
			const char *state;
			int64_t control_age;

			latest_payload(input, "drive.input", &in.input_payload, &in.input_age_ms);
			latest_payload(control, "vehicle.control", &in.control_payload, &control_age);
			manual_step(&cfg, &in, &cmd, &state);
			xm_drive_command_json(json, sizeof(json), cmd.generation, cmd.v_mps, cmd.steer_rad);
			xm_zclient_publish(&client, events, "drive.command", json); /* 끊겨 있으면 버린다 (QoS 0) */

			if (now >= next_beat) {
				xm_app_heartbeat_json(json, sizeof(json), state, now - start);
				xm_zclient_publish(&client, events, "app.heartbeat", json);
				next_beat += MANUAL_HEARTBEAT_MS;
			}
			if (state != last_state) {
				LOG_INF("state=%s generation=%u", state, cmd.generation);
				last_state = state;
			}
			/* 마감 시각을 누적해 오차가 쌓이지 않게, 한 주기 넘게 밀리면 다시 맞춘다 */
			next_tick += MANUAL_PERIOD_MS;
			if (now - next_tick > MANUAL_PERIOD_MS) {
				next_tick = now + MANUAL_PERIOD_MS;
			}
		}
		xm_zclient_poll(&client, (int32_t)MAX(next_tick - k_uptime_get(), 0));
	}
	return 0;
}
