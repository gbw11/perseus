/*
 * Zephyr 자율주행 앱 (S15P31E110-259). 계산 main은 20 ms 마다 입력 사본을 읽는다:
 *   최신 perception.result·vehicle.control → auto_step → drive.command 발행, 1초마다 app.heartbeat.
 * 활성일 때 판단이 바뀌면(장애물 정지·인지 끊김 등) perception.event 를 낸다 (메시지 보드 알림).
 * 별도 v1 worker가 수신·keepalive·재연결과 최신 후보 송신을 소유한다.
 * 계약: docs/contracts/mqtt-contract-v1.md §5·§6. 구조는 수동주행 앱(platform/apps/manual/zephyr)과 같다.
 */
#include <zephyr/kernel.h>
#include <zephyr/logging/log.h>
#include <stdio.h>
#include <errno.h>
#include <string.h>

#include "autonomous.h"
#include "xm_zclient.h"

#include <xenova/drive_runtime.h>
LOG_MODULE_REGISTER(xenova_autonomous, LOG_LEVEL_INF);

static struct xm_zclient client;
static void announce(const char *events, enum auto_reason from, const struct auto_status *st)
{
	char detail[48], json[96];
	const char *kind = NULL;

	if (st->reason == AUTO_REASON_OBSTACLE_STOP) {
		kind = "obstacle";
		if (st->nearest_m >= 0.0f) {
			snprintf(detail, sizeof(detail), "stop %d cm", (int)(st->nearest_m * 100.0f + 0.5f));
		} else {
			snprintf(detail, sizeof(detail), "stop");
		}
	} else if (st->reason == AUTO_REASON_NO_PERCEPTION) {
		kind = "perception_lost";
		snprintf(detail, sizeof(detail), "stop");
	} else if (from == AUTO_REASON_OBSTACLE_STOP) {
		kind = "obstacle_cleared";
		snprintf(detail, sizeof(detail), "resume");
	} else if (from == AUTO_REASON_NO_PERCEPTION) {
		kind = "perception_restored";
		snprintf(detail, sizeof(detail), "resume");
	}
	if (kind != NULL && xm_perception_event_json(json, sizeof(json), kind, detail) > 0) {
		xd_publish(&client, events, "perception.event", json);
	}
}

int main(void)
{
	char events[XM_TOPIC_MAX], control[XM_TOPIC_MAX], perception[XM_TOPIC_MAX], json[160];
	struct auto_cfg cfg;
	const char *last_state = NULL;
	enum auto_reason last_reason = AUTO_REASON_NO_PERCEPTION;
	bool seen_perception = false; /* 시작 직후 첫 인지는 "복구" 가 아니다 */

	auto_cfg_default(&cfg);
	xm_topic_app_events(events, sizeof(events), AUTO_APP_ID);
	xm_topic_vehicle_control(control, sizeof(control));
	xm_topic_app_events(perception, sizeof(perception), "perception");

	int rc = xd_start(AUTO_APP_ID, AUTO_PERIOD_MS);
	if (rc) { LOG_ERR("common runtime failed %d", rc); return xd_finish(&client, rc); }

	int64_t start = k_uptime_get();
	int64_t next_beat = start;

	while (!xd_stopping()) {
		int io_rc = xd_client_prepare(&client, control, perception);
		if (io_rc && io_rc != -EAGAIN && io_rc != -EBUSY) { rc = io_rc; break; }
		bool ready = io_rc == 0;
		int64_t now = k_uptime_get();
		uint64_t cycles_us;
		if ((rc = xdiag_clock_us(&cycles_us))) { break; }
		if (cycles_us >= xd_next_release_us()) {
			if (xd_iteration_begin()) { break; }
			struct auto_inputs in;
			struct auto_command cmd;
			struct auto_status st;
			int64_t control_age, input_seq, control_seq;

			xd_latest(&client, perception, "perception.result", &in.perception_payload, &in.perception_age_ms, &input_seq);
			xd_latest(&client, control, "vehicle.control", &in.control_payload, &control_age, &control_seq);
			if (xd_step_begin()) { break; }
			auto_step(&cfg, &in, &cmd, &st);
			if (xd_step_end()) { break; }
			int encoded = xm_drive_command_json(json, sizeof(json), cmd.generation, cmd.v_mps, cmd.steer_rad);
			xd_command(&client, events, json, encoded, cmd.generation, cmd.v_mps, cmd.steer_rad, input_seq, control_seq, in.perception_age_ms, cfg.perception_timeout_ms);

			if (ready && !xd_stopping() && now >= next_beat) {
				xm_app_heartbeat_json(json, sizeof(json), st.state, now - start);
				xd_publish(&client, events, "app.heartbeat", json);
				next_beat += ((now-next_beat)/AUTO_HEARTBEAT_MS+1)*AUTO_HEARTBEAT_MS;
			}
			if (st.reason != last_reason) {
				/* 대기 중 판단 변화는 알리지 않는다 — 차를 모는 쪽이 아니므로 알림이 헷갈린다 */
				if (!xd_stopping() && strcmp(st.state, "active") == 0 && seen_perception) {
					announce(events, last_reason, &st);
				}
				seen_perception = seen_perception || st.reason != AUTO_REASON_NO_PERCEPTION;
				LOG_INF("reason=%s nearest=%d cm", auto_reason_name(st.reason),
					st.nearest_m >= 0.0f ? (int)(st.nearest_m * 100.0f) : -1);
				last_reason = st.reason;
			}
			if (st.state != last_state) {
				LOG_INF("state=%s generation=%u", st.state, cmd.generation);
				last_state = st.state;
			}
			/* Core and app share one fixed origin. Expired releases are skipped,
			 * not resynchronized to now+period or replayed as a burst. */
			uint64_t end_us;
			if ((rc = xdiag_clock_us(&end_us))) { break; }
			int32_t wait_ms = end_us < xd_next_release_us() ?
				(int32_t)((xd_next_release_us() - end_us + 999U) / 1000U) : 0;
			if (!xd_stopping()) { k_msleep(wait_ms); }
			xd_iteration_end();
		} else { k_sleep(K_MSEC(1)); }
	}
	return xd_finish(&client, rc);
}
