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

#include <xenova/drive_runtime.h>
LOG_MODULE_REGISTER(xenova_manual, LOG_LEVEL_INF);

static struct xm_zclient client;
int main(void)
{
	char events[XM_TOPIC_MAX], control[XM_TOPIC_MAX], input[XM_TOPIC_MAX], json[160];
	struct manual_cfg cfg;
	const char *last_state = NULL;

	manual_cfg_default(&cfg);
	xm_topic_app_events(events, sizeof(events), MANUAL_APP_ID);
	xm_topic_vehicle_control(control, sizeof(control));
	xm_topic_manual_input(input, sizeof(input));

	int rc = xd_start(MANUAL_APP_ID, MANUAL_PERIOD_MS);
	if (rc) { LOG_ERR("common runtime failed %d", rc); return xd_finish(&client, rc); }

	int64_t start = k_uptime_get();
	int64_t next_beat = start;

	while (!xd_stopping()) {
		bool ready = xd_client_prepare(&client, control, input) == 0;
		int64_t now = k_uptime_get();
		uint64_t cycles_us;
		if ((rc = xdiag_clock_us(&cycles_us))) { break; }
		if (cycles_us >= xd_next_release_us()) {
			if (xd_iteration_begin()) { break; }
			struct manual_inputs in;
			struct manual_command cmd;
			const char *state;
			int64_t control_age, input_seq, control_seq;

			xd_latest(&client, input, "drive.input", &in.input_payload, &in.input_age_ms, &input_seq);
			xd_latest(&client, control, "vehicle.control", &in.control_payload, &control_age, &control_seq);
			if (xd_step_begin()) { break; }
			manual_step(&cfg, &in, &cmd, &state);
			if (xd_step_end()) { break; }
			int encoded = xm_drive_command_json(json, sizeof(json), cmd.generation, cmd.v_mps, cmd.steer_rad);
			xd_command(&client, events, json, encoded, cmd.generation, cmd.v_mps, cmd.steer_rad, input_seq, control_seq, in.input_age_ms);

			if (ready && !xd_stopping() && now >= next_beat) {
				xm_app_heartbeat_json(json, sizeof(json), state, now - start);
				xd_publish(&client, events, "app.heartbeat", json);
				next_beat += MANUAL_HEARTBEAT_MS;
			}
			if (state != last_state) {
				LOG_INF("state=%s generation=%u", state, cmd.generation);
				last_state = state;
			}
			/* Core and app share one fixed origin. Expired releases are skipped,
			 * not resynchronized to now+period or replayed as a burst. */
			uint64_t end_us;
			if ((rc = xdiag_clock_us(&end_us))) { break; }
			int32_t wait_ms = end_us < xd_next_release_us() ?
				(int32_t)((xd_next_release_us() - end_us + 999U) / 1000U) : 0;
			if (!xd_stopping() && ready) { xd_poll(&client, wait_ms); }
			else if (!xd_stopping()) { k_msleep(wait_ms); }
			xd_iteration_end();
		} else { k_sleep(K_MSEC(1)); }
	}
	return xd_finish(&client, rc);
}
