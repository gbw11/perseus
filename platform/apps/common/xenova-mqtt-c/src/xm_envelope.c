/* 공통 봉투 생성·검사 — Python xenova_mqtt/envelope.py build·validate 와 같은 순서·오류 코드 */
#include <errno.h>
#include <string.h>

#include "xm_internal.h"

static bool text(const char *s)
{
	return s != NULL && *s != '\0';
}

int xm_identity_init(struct xm_identity *id, const char *vm_id, const char *source_id, const char *app_id,
		     const char *boot_id, const char *instance_id)
{
	if (text(vm_id) == text(source_id)) {
		return -EINVAL; /* vm_id 와 source_id 중 정확히 하나 */
	}
	if (!text(boot_id) || !text(instance_id) || strlen(boot_id) > XM_UUID_LEN ||
	    strlen(instance_id) > XM_UUID_LEN) {
		return -EINVAL;
	}
	id->vm_id = text(vm_id) ? vm_id : NULL;
	id->source_id = text(source_id) ? source_id : NULL;
	id->app_id = text(app_id) ? app_id : NULL;
	strcpy(id->boot_id, boot_id);
	strcpy(id->instance_id, instance_id);
	return 0;
}

void xm_uuid4_format(char out[XM_UUID_LEN + 1], const uint8_t random16[16])
{
	static const char hex[] = "0123456789abcdef";
	uint8_t b[16];
	size_t p = 0;

	memcpy(b, random16, 16);
	b[6] = (uint8_t)((b[6] & 0x0f) | 0x40); /* version 4 */
	b[8] = (uint8_t)((b[8] & 0x3f) | 0x80); /* RFC 4122 variant */
	for (int i = 0; i < 16; i++) {
		if (i == 4 || i == 6 || i == 8 || i == 10) {
			out[p++] = '-';
		}
		out[p++] = hex[b[i] >> 4];
		out[p++] = hex[b[i] & 0xf];
	}
	out[p] = '\0';
}

int xm_envelope_encode(char *out, size_t n, const struct xm_identity *id, const char *topic, const char *type,
		       uint32_t seq, const char *message_id, const char *sent_at, const char *payload_json)
{
	struct xm_topic_info info;
	size_t pos = 0;
	int rc = 0;

	if (!text(type) || !text(message_id) || !text(sent_at) || payload_json == NULL || payload_json[0] != '{' ||
	    seq == 0 || n == 0) {
		return -EINVAL;
	}
	/* app_id: 토픽(apps/{app_id}/events) → identity 순 (Python client.publish → build 와 같다) */
	xm_topic_parse(topic, &info);
	const char *app = info.app_id[0] ? info.app_id : id->app_id;

	out[0] = '\0';
	rc |= xm_put(out, n, &pos, "{\"schema_version\":\"" XM_SCHEMA_VERSION "\",\"message_id\":");
	rc |= xm_put_json_string(out, n, &pos, message_id);
	rc |= xm_put(out, n, &pos, id->vm_id ? ",\"vm_id\":" : ",\"source_id\":");
	rc |= xm_put_json_string(out, n, &pos, id->vm_id ? id->vm_id : id->source_id);
	if (app != NULL) {
		rc |= xm_put(out, n, &pos, ",\"app_id\":");
		rc |= xm_put_json_string(out, n, &pos, app);
	}
	rc |= xm_put(out, n, &pos, ",\"boot_id\":");
	rc |= xm_put_json_string(out, n, &pos, id->boot_id);
	rc |= xm_put(out, n, &pos, ",\"agent_instance_id\":");
	rc |= xm_put_json_string(out, n, &pos, id->instance_id);
	rc |= xm_put(out, n, &pos, ",\"type\":");
	rc |= xm_put_json_string(out, n, &pos, type);
	rc |= xm_put(out, n, &pos, ",\"seq\":");
	rc |= xm_put_uint(out, n, &pos, seq);
	rc |= xm_put(out, n, &pos, ",\"sent_at\":");
	rc |= xm_put_json_string(out, n, &pos, sent_at);
	rc |= xm_put(out, n, &pos, ",\"payload\":");
	rc |= xm_put(out, n, &pos, payload_json);
	rc |= xm_put(out, n, &pos, "}");
	if (rc) {
		return -ENOSPC;
	}
	if (pos > xm_message_max_bytes(type,(struct xm_span){payload_json,(uint16_t)strlen(payload_json)})) {
		return -EMSGSIZE;
	}
	return (int)pos;
}

/* Python _text: 문자열이고 strip() 했을 때 비어 있지 않음 */
static bool has_text(const struct xm_json *j, int i)
{
	if (!xm_json_is_string(j, i)) {
		return false;
	}
	struct xm_span s = xm_json_span(j, i);

	for (uint16_t k = 0; k < s.len; k++) {
		char c = s.p[k];

		if (c != ' ' && c != '\t' && c != '\n' && c != '\r' && c != '\v' && c != '\f') {
			return true;
		}
	}
	return false;
}

static int fail(const char **code, const char *value)
{
	*code = value;
	return -EINVAL;
}

/* 봉투 필드가 문자열이면 원문 span, 아니면 빈 span */
static struct xm_span field(const struct xm_json *j, const char *key)
{
	int i = xm_json_get(j, 0, key);
	struct xm_span none = {NULL, 0};

	return xm_json_is_string(j, i) ? xm_json_span(j, i) : none;
}

/* 토픽 id 와 봉투 필드가 같은가. 필드가 없거나 문자열이 아니면 다르다 (Python None != "x") */
static bool same(struct xm_span s, const char *expected)
{
	return s.p != NULL && xm_span_eq(s, expected);
}

int xm_envelope_decode(const char *topic, const char *json, size_t len, struct xm_envelope *env, const char **code)
{
	static struct xm_json j; /* 스택을 아끼려고 정적. 호출자 한 스레드 전제 (설계 §5) */
	static const char *const required[] = {"message_id", "type", "sent_at", "boot_id", "agent_instance_id"};

	memset(env, 0, sizeof(*env));
	env->seq = -1;
	*code = NULL;

	if (xm_json_parse(&j, json, len) != 0 || !xm_json_is_object(&j, 0)) {
		return fail(code, "PAYLOAD_INVALID");
	}
	if (!xm_span_eq(field(&j, "schema_version"), XM_SCHEMA_VERSION)) {
		return fail(code, "SCHEMA_VERSION_UNSUPPORTED");
	}
	bool command = xm_span_eq(field(&j, "type"), "agent.command");
	size_t nreq = command ? 3 : 5;

	if (!command) {
		int64_t seq;

		if (xm_json_int(&j, xm_json_get(&j, 0, "seq"), &seq) != 0 || seq < 1) {
			return fail(code, "PAYLOAD_INVALID");
		}
		env->seq = seq;
	}
	for (size_t k = 0; k < nreq; k++) {
		if (!has_text(&j, xm_json_get(&j, 0, required[k]))) {
			return fail(code, "PAYLOAD_INVALID");
		}
	}
	int payload = xm_json_get(&j, 0, "payload");

	if (!xm_json_is_object(&j, payload)) {
		return fail(code, "PAYLOAD_INVALID");
	}
	if (!has_text(&j, xm_json_get(&j, 0, "vm_id")) && !has_text(&j, xm_json_get(&j, 0, "source_id"))) {
		return fail(code, "PAYLOAD_INVALID");
	}

	env->message_id = field(&j, "message_id");
	env->vm_id = field(&j, "vm_id");
	env->source_id = field(&j, "source_id");
	env->app_id = field(&j, "app_id");
	env->boot_id = field(&j, "boot_id");
	env->instance_id = field(&j, "agent_instance_id");
	env->type = field(&j, "type");
	env->sent_at = field(&j, "sent_at");
	env->payload = xm_json_span(&j, payload);

	struct xm_topic_info info;

	xm_topic_parse(topic, &info);
	if (info.vm_id[0] && !same(env->vm_id, info.vm_id)) {
		return fail(code, "VM_ID_MISMATCH");
	}
	if (info.app_id[0] && !same(env->app_id, info.app_id)) {
		return fail(code, "APP_ID_MISMATCH");
	}
	return 0;
}
