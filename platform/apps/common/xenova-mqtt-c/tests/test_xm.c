/*
 * 호스트(Linux) 단위 시험. tests/vectors 의 JSON 은 Python 시험(mqtt-client/tests/test_vectors.py)과 같은 파일이다.
 *   test_xm <vectors 디렉터리>
 */
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define JSMN_HEADER
#define JSMN_STRICT
#define JSMN_PARENT_LINKS
#include "jsmn.h"
#include "xenova/mqtt/xm.h"

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

/* ---- 벡터 파일 읽기 -------------------------------------------------- */

#define MAX_TOK 8192
static char *js;
static jsmntok_t tok[MAX_TOK];
static int ntok;

static int load(const char *dir, const char *name)
{
	char path[512];
	snprintf(path, sizeof(path), "%s/%s.json", dir, name);
	FILE *f = fopen(path, "rb");
	if (!f) {
		fprintf(stderr, "cannot open %s\n", path);
		exit(2);
	}
	fseek(f, 0, SEEK_END);
	long len = ftell(f);
	fseek(f, 0, SEEK_SET);
	free(js);
	js = malloc((size_t)len + 1);
	if (fread(js, 1, (size_t)len, f) != (size_t)len) {
		exit(2);
	}
	js[len] = '\0';
	fclose(f);
	jsmn_parser p;
	jsmn_init(&p);
	ntok = jsmn_parse(&p, js, (size_t)len, tok, MAX_TOK);
	if (ntok < 1) {
		fprintf(stderr, "bad vector file %s (%d)\n", path, ntok);
		exit(2);
	}
	return 0;
}

static int skip(int i)
{
	int end = tok[i].end;
	for (i++; i < ntok && tok[i].start < end; i++) {
	}
	return i;
}

static int get(int obj, const char *key)
{
	int i = obj + 1;
	for (int k = 0; k < tok[obj].size; k++) {
		if ((int)strlen(key) == tok[i].end - tok[i].start && !strncmp(js + tok[i].start, key, strlen(key))) {
			return i + 1;
		}
		i = skip(i + 1);
	}
	return -1;
}

/* 배열 obj 의 n 번째 원소 */
static int elem(int arr, int n)
{
	int i = arr + 1;
	for (int k = 0; k < n; k++) {
		i = skip(i);
	}
	return i;
}

static bool is_null(int i)
{
	return i < 0 || (tok[i].type == JSMN_PRIMITIVE && js[tok[i].start] == 'n');
}

static const char *str(int i, char *buf, size_t n)
{
	int len = tok[i].end - tok[i].start;
	snprintf(buf, n, "%.*s", len, js + tok[i].start);
	return buf;
}

static long num(int i)
{
	return strtol(js + tok[i].start, NULL, 10);
}

static bool boolean(int i)
{
	return js[tok[i].start] == 't';
}

/* ---- 벡터 시험 ------------------------------------------------------- */

static int build(const char *fn, int args, char *out, size_t n)
{
	char a[128] = "", b[128] = "";
	if (tok[args].size > 0) {
		str(elem(args, 0), a, sizeof(a));
	}
	if (tok[args].size > 1) {
		str(elem(args, 1), b, sizeof(b));
	}
	if (!strcmp(fn, "vm")) return xm_topic_vm(out, n, a, b);
	if (!strcmp(fn, "app_events")) return xm_topic_app_events(out, n, a);
	if (!strcmp(fn, "vm_app_events")) return xm_topic_vm_app_events(out, n, a, b);
	if (!strcmp(fn, "manual_input")) return xm_topic_manual_input(out, n);
	if (!strcmp(fn, "vehicle_control")) return xm_topic_vehicle_control(out, n);
	if (!strcmp(fn, "vehicle_status")) return xm_topic_vehicle_status(out, n);
	if (!strcmp(fn, "probe_request")) return xm_topic_probe_request(out, n, a);
	if (!strcmp(fn, "probe_reply")) return xm_topic_probe_reply(out, n, a);
	return -999;
}

static void vectors_topics(const char *dir)
{
	char fn[64], out[XM_TOPIC_MAX], want[XM_TOPIC_MAX], topic[XM_TOPIC_MAX], filt[XM_TOPIC_MAX];
	load(dir, "topics");

	int arr = get(0, "build");
	for (int k = 0; k < tok[arr].size; k++) {
		int c = elem(arr, k);
		int rc = build(str(get(c, "fn"), fn, sizeof(fn)), get(c, "args"), out, sizeof(out));
		str(get(c, "out"), want, sizeof(want));
		CHECK(rc > 0 && !strcmp(out, want), "build %s → %s (rc %d), want %s", fn, out, rc, want);
	}
	arr = get(0, "invalid");
	for (int k = 0; k < tok[arr].size; k++) {
		int c = elem(arr, k);
		int rc = build(str(get(c, "fn"), fn, sizeof(fn)), get(c, "args"), out, sizeof(out));
		CHECK(rc == -EINVAL, "invalid %s should be -EINVAL, got %d", fn, rc);
	}
	arr = get(0, "parse");
	for (int k = 0; k < tok[arr].size; k++) {
		int c = elem(arr, k);
		struct xm_topic_info info;
		char vm[XM_ID_MAX] = "", app[XM_ID_MAX] = "";
		xm_topic_parse(str(get(c, "topic"), topic, sizeof(topic)), &info);
		if (!is_null(get(c, "vm_id"))) str(get(c, "vm_id"), vm, sizeof(vm));
		if (!is_null(get(c, "app_id"))) str(get(c, "app_id"), app, sizeof(app));
		CHECK(!strcmp(info.vm_id, vm) && !strcmp(info.app_id, app), "parse %s → (%s,%s)", topic, info.vm_id,
		      info.app_id);
	}
	arr = get(0, "matches");
	for (int k = 0; k < tok[arr].size; k++) {
		int c = elem(arr, k);
		str(elem(c, 0), filt, sizeof(filt));
		str(elem(c, 1), topic, sizeof(topic));
		CHECK(xm_topic_matches(filt, topic) == boolean(elem(c, 2)), "matches %s ~ %s", filt, topic);
	}
}

static void vectors_policy(const char *dir)
{
	char type[64];
	load(dir, "policy");
	for (int k = 0; k < tok[0].size; k++) {
		int c = elem(0, k);
		const struct xm_policy *p = xm_policy_for(str(get(c, "type"), type, sizeof(type)));
		CHECK(p->qos == num(get(c, "qos")) && p->retain == boolean(get(c, "retain")) &&
			      xm_policy_max_bytes(p) == (size_t)num(get(c, "max_bytes")),
		      "policy %s", type);
	}
}

static void vectors_validate(const char *dir)
{
	char name[64], topic[XM_TOPIC_MAX], want[64], raw[1024];
	load(dir, "validate");
	for (int k = 0; k < tok[0].size; k++) {
		int c = elem(0, k);
		const char *body;
		size_t len;
		str(get(c, "name"), name, sizeof(name));
		str(get(c, "topic"), topic, sizeof(topic));
		int r = get(c, "raw");
		if (r >= 0) {
			/* raw 는 JSON 문자열로 저장돼 있다. 벡터의 raw 는 \" 이스케이프만 쓴다 */
			size_t o = 0;
			for (int p = tok[r].start; p < tok[r].end && o + 1 < sizeof(raw); p++) {
				if (js[p] == '\\' && p + 1 < tok[r].end) p++;
				raw[o++] = js[p];
			}
			raw[o] = '\0';
			body = raw;
			len = o;
		} else {
			int e = get(c, "envelope");
			body = js + tok[e].start;
			len = (size_t)(tok[e].end - tok[e].start);
		}
		struct xm_envelope env;
		const char *code = NULL;
		int rc = xm_envelope_decode(topic, body, len, &env, &code);
		int w = get(c, "error");
		if (is_null(w)) {
			CHECK(rc == 0, "validate %s should pass, got %s", name, code ? code : "?");
		} else {
			str(w, want, sizeof(want));
			CHECK(rc < 0 && code && !strcmp(code, want), "validate %s → %s, want %s", name,
			      code ? code : "(pass)", want);
		}
	}
}

static struct xm_span sp(const char *s)
{
	struct xm_span v = {s, (uint16_t)strlen(s)};
	return v;
}

static void vectors_seq(const char *dir)
{
	static char strs[64][4][64];
	load(dir, "seq");
	for (int k = 0; k < tok[0].size; k++) {
		int c = elem(0, k), steps = get(c, "steps");
		struct xm_seq_tracker t;
		xm_seq_init(&t);
		for (int s = 0; s < tok[steps].size; s++) {
			int st = elem(steps, s);
			struct xm_envelope env;
			memset(&env, 0, sizeof(env));
			env.vm_id = sp(str(get(st, "publisher"), strs[s][0], 64));
			env.boot_id = sp(str(get(st, "boot_id"), strs[s][1], 64));
			env.instance_id = sp(str(get(st, "instance_id"), strs[s][2], 64));
			env.seq = is_null(get(st, "seq")) ? -1 : num(get(st, "seq"));
			str(get(st, "topic"), strs[s][3], 64);
			bool got = xm_seq_accept(&t, strs[s][3], &env);
			CHECK(got == boolean(get(st, "accept")), "seq step %d", s);
		}
	}
}

/* ---- 벡터 밖 시험 ---------------------------------------------------- */

static void test_encode_roundtrip(void)
{
	struct xm_identity id;
	char topic[XM_TOPIC_MAX], payload[128], buf[XM_MAX_DRIVING_BYTES];
	const char *code;
	struct xm_envelope env;

	CHECK(xm_identity_init(&id, "vm-1", "dom0", NULL, "b", "i") == -EINVAL, "vm_id and source_id both");
	CHECK(xm_identity_init(&id, NULL, NULL, NULL, "b", "i") == -EINVAL, "neither vm_id nor source_id");
	CHECK(xm_identity_init(&id, "22222222-2222-4222-8222-222222222222", NULL, "manual",
			       "c5b0b0b2-0000-4000-8000-000000000001", "c5b0b0b2-0000-4000-8000-000000000002") == 0,
	      "identity");
	xm_topic_app_events(topic, sizeof(topic), "manual");
	xm_drive_command_json(payload, sizeof(payload), 7, 1.0f, -0.4f);
	CHECK(!strcmp(payload, "{\"generation\":7,\"v_mps\":1.0000,\"steer_rad\":-0.4000}"), "drive json %s", payload);

	int len = xm_envelope_encode(buf, sizeof(buf), &id, topic, "drive.command", 42, "17", "1970-01-01T00:00:01.000Z",
				     payload);
	CHECK(len > 0 && len < (int)XM_MAX_DRIVING_BYTES, "encode len %d", len);
	CHECK(xm_envelope_decode(topic, buf, (size_t)len, &env, &code) == 0, "decode own envelope: %s", code);
	CHECK(env.seq == 42 && xm_span_eq(env.type, "drive.command") && xm_span_eq(env.app_id, "manual"),
	      "roundtrip fields");
	printf("encoded drive.command (%d bytes): %s\n", len, buf);

	/* 토픽의 app_id 가 identity 보다 우선 */
	xm_topic_app_events(topic, sizeof(topic), "autonomous");
	len = xm_envelope_encode(buf, sizeof(buf), &id, topic, "drive.command", 1, "1", "t", payload);
	CHECK(xm_envelope_decode(topic, buf, (size_t)len, &env, &code) == 0 && xm_span_eq(env.app_id, "autonomous"),
	      "topic app_id wins");

	/* 주행 type 은 1 KB 한도 */
	static char big[2048], out[4096];
	memset(big, 'x', sizeof(big));
	memcpy(big, "{\"pad\":\"", 8);
	memcpy(big + sizeof(big) - 3, "\"}", 3);
	CHECK(xm_envelope_encode(out, sizeof(out), &id, topic, "drive.command", 1, "1", "t", big) == -EMSGSIZE,
	      "driving 1 KB limit");
	CHECK(xm_envelope_encode(out, sizeof(out), &id, topic, "message.posted", 1, "1", "t", big) > 1024,
	      "non-driving allows > 1 KB");
	CHECK(xm_envelope_encode(out, 64, &id, topic, "drive.command", 1, "1", "t", payload) == -ENOSPC, "ENOSPC");
	CHECK(xm_envelope_encode(out, sizeof(out), &id, topic, "drive.command", 0, "1", "t", payload) == -EINVAL,
	      "seq 0 rejected");

	/* source_id 발행자, 따옴표 이스케이프 */
	CHECK(xm_identity_init(&id, NULL, "dom0-\"gw", NULL, "b", "i") == 0, "identity source");
	xm_topic_vehicle_control(topic, sizeof(topic));
	len = xm_envelope_encode(buf, sizeof(buf), &id, topic, "vehicle.control", 1, "1", "t", "{}");
	CHECK(len > 0 && strstr(buf, "\"source_id\":\"dom0-\\\"gw\"") && !strstr(buf, "app_id"), "source_id %s", buf);
	CHECK(xm_envelope_decode(topic, buf, (size_t)len, &env, &code) == 0, "decode source_id envelope");
}

static void test_payloads(void)
{
	char buf[128];
	struct xm_vehicle_control vc;
	uint32_t keys;

	xm_app_heartbeat_json(buf, sizeof(buf), "active", 12345);
	CHECK(!strcmp(buf, "{\"state\":\"active\",\"uptime_s\":12.345}"), "heartbeat %s", buf);
	xm_drive_command_json(buf, sizeof(buf), 0, -0.00001f, 0.0f);
	CHECK(!strcmp(buf, "{\"generation\":0,\"v_mps\":0.0000,\"steer_rad\":0.0000}"), "no negative zero %s", buf);

	CHECK(xm_vehicle_control_parse(sp("{\"generation\":3,\"mode\":\"MANUAL\",\"active_app\":\"manual\"}"), &vc) == 0 &&
		      vc.generation == 3 && !strcmp(vc.mode, "MANUAL") && !strcmp(vc.active_app, "manual"),
	      "vehicle.control");
	CHECK(xm_vehicle_control_parse(sp("{\"generation\":4,\"mode\":\"SAFE_STOP\",\"active_app\":null}"), &vc) == 0 &&
		      vc.active_app[0] == '\0',
	      "active_app null");
	const char *bad[] = {"{\"generation\":-1}", "{\"generation\":true}", "{\"generation\":1.5}",
			     "{\"generation\":\"7\"}", "{}", "[]", "{\"generation\":4294967296}"};
	for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
		CHECK(xm_vehicle_control_parse(sp(bad[i]), &vc) == -EINVAL, "bad generation %s", bad[i]);
	}

	CHECK(xm_drive_input_parse(sp("{\"keys\":[\"up\",\"left\",\"ArrowUp\",3,{\"k\":1},\"brake\"]}"), &keys) == 0 &&
		      keys == (XM_KEY_UP | XM_KEY_LEFT | XM_KEY_BRAKE),
	      "drive.input keys %x", keys);
	CHECK(xm_drive_input_parse(sp("{\"keys\":[]}"), &keys) == 0 && keys == 0, "empty keys");
	CHECK(xm_drive_input_parse(sp("{\"keys\":\"up\"}"), &keys) == -EINVAL, "keys not array");
	CHECK(xm_drive_input_parse(sp("{}"), &keys) == -EINVAL, "keys missing");

	struct xm_auto_delay_command command;
	const char *delay="{\"command_id\":\"cmd-1\",\"operation_id\":\"op-1\",\"expected_boot_id\":\"boot\",\"expires_at\":\"2026-11-06T00:00:00Z\",\"action\":\"auto_delay\",\"parameters\":{\"delay_ms\":20,\"duration_s\":30}}";
	CHECK(xm_auto_delay_command_parse(sp(delay),&command)==0&&command.delay_ms==20&&command.duration_s==30,
	      "auto delay command");
	CHECK(xm_auto_delay_command_parse(sp("{\"command_id\":\"c\",\"expected_boot_id\":\"b\",\"expires_at\":\"t\",\"action\":\"auto_delay\",\"parameters\":{\"delay_ms\":101,\"duration_s\":30}}"),&command)==-ERANGE,
	      "auto delay bounds");
	CHECK(xm_auto_delay_command_parse(sp("{\"command_id\":\"c\",\"expected_boot_id\":\"b\",\"expires_at\":\"t\",\"action\":\"auto_delay\",\"parameters\":{\"delay_ms\":20,\"duration_s\":30,\"extra\":1}}"),&command)==-ERANGE,
	      "auto delay extra parameter");
}

static void test_uuid(void)
{
	uint8_t r[16];
	char out[XM_UUID_LEN + 1];
	memset(r, 0xff, sizeof(r));
	xm_uuid4_format(out, r);
	CHECK(strlen(out) == 36 && out[8] == '-' && out[14] == '4' && (out[19] == 'b' || out[19] == 'a' ||
		      out[19] == '8' || out[19] == '9'), "uuid %s", out);
}

static void test_lifecycle(void)
{
 char out[256];
 CHECK(xm_app_lifecycle_json(out,sizeof(out),42,1,0,"WAITING","QUALIFIED",NULL)>0,"waiting qualification");
 CHECK(strstr(out,"\"progress_seq\":\"0\"")&&strstr(out,"\"profile\":\"warm-drive-1\""),"distinct supervision %s",out);
 CHECK(xm_app_lifecycle_json(out,sizeof(out),42,1,0,"EXECUTING","READY",NULL)==-EINVAL,"no fabricated progress");
 CHECK(xm_app_lifecycle_json(out,sizeof(out),42,1,1,"WAITING","READY",NULL)==-EINVAL,"waiting is not activation");
 CHECK(xm_app_lifecycle_json(out,sizeof(out),42,UINT64_MAX,UINT64_MAX,"EXECUTING","READY",NULL)>0,"full counters");
 CHECK(xm_app_lifecycle_json(out,16,42,1,1,"EXECUTING","READY",NULL)==-ENOSPC,"bounded serialization");
}

int main(int argc, char **argv)
{
	const char *dir = argc > 1 ? argv[1] : "tests/vectors";

	vectors_topics(dir);
	vectors_policy(dir);
	vectors_validate(dir);
	vectors_seq(dir);
	test_encode_roundtrip();
	test_payloads();
	test_uuid();
	test_lifecycle();
	printf("%d checks, %d failures\n", checks, failures);
	return failures ? 1 : 0;
}
