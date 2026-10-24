/* 자율주행 로직 호스트 시험 — 규칙은 src/autonomous.h */
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "autonomous.h"

static int failures, checks;
#define CHECK(cond, ...)                                                                                       \
	do {                                                                                                   \
		checks++;                                                                                      \
		if (!(cond)) {                                                                                 \
			failures++;                                                                            \
			fprintf(stderr, "FAIL %s:%d: ", __FILE__, __LINE__);                                    \
			fprintf(stderr, __VA_ARGS__);                                                          \
			fputc('\n', stderr);                                                                   \
		}                                                                                              \
	} while (0)

static struct xm_span sp(const char *s)
{
	struct xm_span v = {s, s ? (uint16_t)strlen(s) : 0};
	return v;
}

static bool near(float a, float b)
{
	return fabsf(a - b) < 1e-4f;
}

static const char *CONTROL_AUTO = "{\"generation\":5,\"mode\":\"AUTO\",\"active_app\":\"autonomous\"}";
static const char *CONTROL_MANUAL = "{\"generation\":6,\"mode\":\"MANUAL\",\"active_app\":\"manual\"}";

static void step(const char *perception, int64_t age, const char *control, struct auto_command *cmd,
		 struct auto_status *st)
{
	struct auto_cfg cfg;
	struct auto_inputs in = {sp(perception), age, sp(control)};

	auto_cfg_default(&cfg);
	auto_step(&cfg, &in, cmd, st);
}

static void test_lane_keeping(void)
{
	struct auto_command cmd;
	struct auto_status st;

	step("{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[]}", 10, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.v_mps, 0.5f) && near(cmd.steer_rad, 0) && cmd.generation == 5 && !strcmp(st.state, "active") &&
		      st.reason == AUTO_REASON_CRUISE,
	      "straight (%f,%f) %s", cmd.v_mps, cmd.steer_rad, auto_reason_name(st.reason));

	/* 차가 차선 중심보다 왼쪽 10 cm → 오른쪽(−)으로 */
	step("{\"lane_offset_m\":0.1,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[]}", 10, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.steer_rad, -0.15f) && cmd.v_mps < 0.5f && cmd.v_mps > 0.4f, "left offset (%f,%f)", cmd.v_mps,
	      cmd.steer_rad);

	/* 차선이 왼쪽으로 돈다 → 왼쪽(+)으로 */
	step("{\"lane_offset_m\":0,\"heading_err_rad\":0.2,\"stop\":false,\"obstacles\":[]}", 10, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.steer_rad, 0.2f), "left curve steer %f", cmd.steer_rad);

	/* 최대 조향으로 자르고, 그때 속도는 turn_slowdown 만큼 준다 */
	step("{\"lane_offset_m\":-1,\"heading_err_rad\":1,\"stop\":false,\"obstacles\":[]}", 10, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.steer_rad, 0.4f) && near(cmd.v_mps, 0.3f), "clamp (%f,%f)", cmd.v_mps, cmd.steer_rad);
}

static void test_obstacles(void)
{
	struct auto_command cmd;
	struct auto_status st;

	/* 통로 안 1.2 m → 감속 (1.2−0.8)/(1.6−0.8) = 절반 */
	step("{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[{\"x\":1.2,\"y\":0.05,\"w\":0.1}]}",
	     10, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.v_mps, 0.25f) && st.reason == AUTO_REASON_SLOWING && near(st.nearest_m, 1.2f), "slowing %f",
	      cmd.v_mps);

	/* 통로 밖(옆 차선)은 무시 */
	step("{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[{\"x\":0.5,\"y\":0.4,\"w\":0.1}]}",
	     10, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.v_mps, 0.5f) && st.nearest_m < 0, "outside corridor %f", cmd.v_mps);

	/* 폭이 통로에 걸치면 통로 안: |y| 0.19 ≤ 0.15 + 0.1/2 */
	step("{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[{\"x\":0.7,\"y\":-0.19,\"w\":0.1}]}",
	     10, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.v_mps, 0) && st.reason == AUTO_REASON_OBSTACLE_STOP, "edge in corridor %f", cmd.v_mps);

	/* 인지가 stop 이면 거리와 상관없이 정지 */
	step("{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":true,\"obstacles\":[]}", 10, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.v_mps, 0) && st.reason == AUTO_REASON_OBSTACLE_STOP, "stop flag %f", cmd.v_mps);

	/* 가장 가까운 것을 고른다 (순서가 섞여 와도) */
	step("{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[{\"x\":1.5,\"y\":0,\"w\":0.1},"
	     "{\"x\":1.0,\"y\":0,\"w\":0.1},{\"x\":-0.3,\"y\":0,\"w\":0.1}]}",
	     10, CONTROL_AUTO, &cmd, &st);
	CHECK(near(st.nearest_m, 1.0f) && near(cmd.v_mps, 0.125f), "nearest %f v %f", st.nearest_m, cmd.v_mps);
}

static void test_perception_loss(void)
{
	struct auto_command cmd;
	struct auto_status st;
	const char *ok = "{\"lane_offset_m\":0.1,\"heading_err_rad\":0.1,\"stop\":false,\"obstacles\":[]}";

	step(ok, AUTO_PERCEPTION_TIMEOUT_MS, CONTROL_AUTO, &cmd, &st);
	CHECK(cmd.v_mps > 0, "fresh at timeout");
	step(ok, AUTO_PERCEPTION_TIMEOUT_MS + 1, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.v_mps, 0) && near(cmd.steer_rad, 0) && st.reason == AUTO_REASON_NO_PERCEPTION && cmd.generation == 5,
	      "stale → stop but keep generation");
	step(NULL, 0, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.v_mps, 0) && st.reason == AUTO_REASON_NO_PERCEPTION, "no perception");
	step("{\"lane_offset_m\":\"x\",\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[]}", 10, CONTROL_AUTO, &cmd, &st);
	CHECK(near(cmd.v_mps, 0) && st.reason == AUTO_REASON_NO_PERCEPTION, "malformed perception");
}

static void test_control(void)
{
	struct auto_command cmd;
	struct auto_status st;
	const char *ok = "{\"lane_offset_m\":0,\"heading_err_rad\":0,\"stop\":false,\"obstacles\":[]}";

	step(ok, 10, CONTROL_MANUAL, &cmd, &st);
	CHECK(!strcmp(st.state, "standby") && cmd.generation == 6 && cmd.v_mps > 0, "standby keeps computing");
	step(ok, 10, NULL, &cmd, &st);
	CHECK(!strcmp(st.state, "idle") && cmd.generation == 0, "no control");
	step(ok, 10, "{\"generation\":4,\"mode\":\"SAFE_STOP\",\"active_app\":null}", &cmd, &st);
	CHECK(!strcmp(st.state, "standby") && cmd.generation == 4, "safe stop");
}

/* 젯슨이 장애물 8개를 낸 봉투도 버퍼·토큰 한도 안에서 풀린다 */
static void test_full_envelope(void)
{
	char payload[512], buf[1200], topic[XM_TOPIC_MAX];
	size_t pos = 0;
	struct xm_identity id;
	struct xm_envelope env;
	struct xm_perception_result r;
	const char *code;

	pos += (size_t)snprintf(payload + pos, sizeof(payload) - pos,
				"{\"lane_offset_m\":-0.0123,\"heading_err_rad\":0.0456,\"stop\":false,\"obstacles\":[");
	for (int i = 0; i < XM_PERCEPTION_MAX_OBSTACLES; i++) {
		pos += (size_t)snprintf(payload + pos, sizeof(payload) - pos, "%s{\"x\":%d.123,\"y\":-0.456,\"w\":0.789}",
					i ? "," : "", i + 1);
	}
	snprintf(payload + pos, sizeof(payload) - pos, "]}");
	xm_topic_app_events(topic, sizeof(topic), "perception");
	CHECK(xm_identity_init(&id, NULL, "jetson", "perception", "b", "i") == 0, "identity");
	int len = xm_envelope_encode(buf, sizeof(buf), &id, topic, "perception.result", 99999, "123", "2026-10-22T00:00:00.000Z",
				     payload);
	CHECK(len > 0 && len < 1280, "envelope %d bytes (RX_BUF 1280)", len);
	CHECK(xm_envelope_decode(topic, buf, (size_t)len, &env, &code) == 0, "decode %s", code ? code : "");
	CHECK(xm_perception_result_parse(env.payload, &r) == 0 && r.n_obstacles == 8 && near(r.obstacles[7].x, 8.123f),
	      "parse 8 obstacles n=%d", r.n_obstacles);
}

static void test_vm_id(void)
{
	char out[XM_UUID_LEN + 1];

	CHECK(auto_vm_id_from_xenstore("/vm/AAAAAAAA-1111-4111-8111-111111111111", out) == 0 &&
		      !strcmp(out, "aaaaaaaa-1111-4111-8111-111111111111"),
	      "vm_id lower");
	CHECK(auto_vm_id_from_xenstore("/vm/00000000-0000-0000-0000-000000000000", out) == -EINVAL, "nil");
	CHECK(auto_vm_id_from_xenstore("vm/aaaaaaaa-1111-4111-8111-111111111111", out) == -EINVAL, "prefix");
	CHECK(auto_vm_id_from_xenstore(NULL, out) == -EINVAL, "null");
}

int main(void)
{
	test_lane_keeping();
	test_obstacles();
	test_perception_loss();
	test_control();
	test_full_envelope();
	test_vm_id();
	printf("%d checks, %d failures\n", checks, failures);
	return failures ? 1 : 0;
}
