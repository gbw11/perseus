/* 수동주행 로직 호스트 시험 — Python tests/test_keymap.py·test_app.py 와 같은 표 */
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "manual.h"

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
	return fabsf(a - b) < 1e-6f;
}

static void test_keymap(void)
{
	struct manual_cfg cfg;
	const struct {
		uint32_t keys;
		float v, steer;
	} table[] = {
		{0, 0, 0},
		{XM_KEY_UP, 1.0f, 0},
		{XM_KEY_DOWN, -0.5f, 0},
		{XM_KEY_LEFT, 0, 0.4f},
		{XM_KEY_RIGHT, 0, -0.4f},
		{XM_KEY_UP | XM_KEY_LEFT, 1.0f, 0.4f},
		{XM_KEY_UP | XM_KEY_DOWN, 0, 0},
		{XM_KEY_LEFT | XM_KEY_RIGHT, 0, 0},
		{XM_KEY_UP | XM_KEY_BRAKE | XM_KEY_RIGHT, 0, -0.4f},
	};
	manual_cfg_default(&cfg);
	for (size_t i = 0; i < sizeof(table) / sizeof(table[0]); i++) {
		float v, s;
		manual_target(&cfg, table[i].keys, &v, &s);
		CHECK(near(v, table[i].v) && near(s, table[i].steer), "keys %x → (%f,%f)", table[i].keys, v, s);
	}
}

static void step(const char *input, int64_t age, const char *control, struct manual_command *cmd, const char **state)
{
	struct manual_cfg cfg;
	struct manual_inputs in = {sp(input), age, sp(control)};
	manual_cfg_default(&cfg);
	manual_step(&cfg, &in, cmd, state);
}

static void test_step(void)
{
	struct manual_command c;
	const char *state;

	step(NULL, 0, NULL, &c, &state);
	CHECK(c.generation == 0 && c.v_mps == 0 && !strcmp(state, "idle"), "no control no input");

	step("{\"keys\":[\"up\",\"left\"]}", 10, NULL, &c, &state);
	CHECK(near(c.v_mps, 1.0f) && near(c.steer_rad, 0.4f), "fresh input drives");

	step("{\"keys\":[\"up\"]}", 250, NULL, &c, &state);
	CHECK(c.v_mps == 0, "stale input stops");
	step("{\"keys\":[\"up\"]}", 200, NULL, &c, &state);
	CHECK(near(c.v_mps, 1.0f), "exactly timeout still fresh (Python: age > timeout)");

	const char *bad[] = {"{}", "{\"keys\":\"up\"}", "{\"keys\":null}"};
	for (size_t i = 0; i < 3; i++) {
		step(bad[i], 0, NULL, &c, &state);
		CHECK(c.v_mps == 0, "malformed input %s stops", bad[i]);
	}

	step(NULL, 0, "{\"generation\":7,\"mode\":\"MANUAL\",\"active_app\":\"manual\"}", &c, &state);
	CHECK(c.generation == 7 && !strcmp(state, "active"), "MANUAL active");
	step(NULL, 0, "{\"generation\":3,\"mode\":\"AUTO_READY\",\"active_app\":\"manual\"}", &c, &state);
	CHECK(!strcmp(state, "active"), "AUTO_READY active");
	step(NULL, 0, "{\"generation\":3,\"mode\":\"AUTO\",\"active_app\":\"autonomous\"}", &c, &state);
	CHECK(c.generation == 3 && !strcmp(state, "standby"), "AUTO standby");
	step(NULL, 0, "{\"generation\":3,\"mode\":\"SAFE_STOP\",\"active_app\":null}", &c, &state);
	CHECK(!strcmp(state, "standby"), "SAFE_STOP standby");

	const char *bad_gen[] = {"{\"generation\":null}", "{\"generation\":-1}", "{\"generation\":\"7\"}",
				 "{\"generation\":1.5}", "{\"generation\":true}"};
	for (size_t i = 0; i < 5; i++) {
		step(NULL, 0, bad_gen[i], &c, &state);
		CHECK(c.generation == 0 && !strcmp(state, "idle"), "bad generation %s", bad_gen[i]);
	}
}

static void test_vm_id(void)
{
	char out[XM_UUID_LEN + 1];

	CHECK(manual_vm_id_from_xenstore("/vm/1B4E28BA-2FA1-11D2-883F-0016D3CCA427", out) == 0 &&
		      !strcmp(out, "1b4e28ba-2fa1-11d2-883f-0016d3cca427"),
	      "vm path → lower uuid (%s)", out);
	const char *bad[] = {NULL, "", "1b4e28ba-2fa1-11d2-883f-0016d3cca427", "/vm/1b4e28ba-2fa1-11d2-883f-0016d3cca42",
			     "/vm/1b4e28ba-2fa1-11d2-883f-0016d3cca4277", "/vm/1b4e28ba_2fa1-11d2-883f-0016d3cca427",
			     "/vm/zb4e28ba-2fa1-11d2-883f-0016d3cca427", "/vm/00000000-0000-0000-0000-000000000000",
			     "/local/domain/3/vm"};
	for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
		CHECK(manual_vm_id_from_xenstore(bad[i], out) == -EINVAL, "bad vm path %s", bad[i] ? bad[i] : "(null)");
	}
}

int main(void)
{
	test_keymap();
	test_step();
	test_vm_id();
	printf("%d checks, %d failures\n", checks, failures);
	return failures ? 1 : 0;
}
