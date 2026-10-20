/* 토픽 빌더·파서 — Python xenova_mqtt/topics.py 와 같은 규칙 */
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "xenova/mqtt/xm.h"

static const char *const VM_KINDS[] = {"heartbeat", "inventory", "metrics", "command", "command-result"};

/* 토픽 한 단계: '/', '+', '#', 공백 없는 비어 있지 않은 문자열 */
static bool segment_ok(const char *s)
{
	if (s == NULL || *s == '\0') {
		return false;
	}
	for (; *s; s++) {
		if (*s == '/' || *s == '+' || *s == '#' || *s == ' ' || *s == '\t' || *s == '\n' || *s == '\r' ||
		    *s == '\v' || *s == '\f') {
			return false;
		}
	}
	return true;
}

static int fmt(char *out, size_t n, const char *suffix_fmt, const char *a, const char *b)
{
	int len = snprintf(out, n, suffix_fmt, XM_PREFIX, a, b);

	if (len < 0) {
		return -EINVAL;
	}
	return (size_t)len >= n ? -ENOSPC : len;
}

int xm_topic_vm(char *out, size_t n, const char *vm_id, const char *kind)
{
	bool known = false;

	for (size_t i = 0; i < sizeof(VM_KINDS) / sizeof(VM_KINDS[0]); i++) {
		known |= kind != NULL && strcmp(kind, VM_KINDS[i]) == 0;
	}
	if (!known || !segment_ok(vm_id)) {
		return -EINVAL;
	}
	return fmt(out, n, "%s/vms/%s/%s", vm_id, kind);
}

int xm_topic_app_events(char *out, size_t n, const char *app_id)
{
	return segment_ok(app_id) ? fmt(out, n, "%s/apps/%s/events%s", app_id, "") : -EINVAL;
}

int xm_topic_manual_input(char *out, size_t n)
{
	return fmt(out, n, "%s/apps/manual/input%s%s", "", "");
}

int xm_topic_vehicle_control(char *out, size_t n)
{
	return fmt(out, n, "%s/vehicle/control%s%s", "", "");
}

int xm_topic_vehicle_status(char *out, size_t n)
{
	return fmt(out, n, "%s/vehicle/status%s%s", "", "");
}

int xm_topic_probe_request(char *out, size_t n, const char *target_vm_id)
{
	return segment_ok(target_vm_id) ? fmt(out, n, "%s/probes/%s/request%s", target_vm_id, "") : -EINVAL;
}

int xm_topic_probe_reply(char *out, size_t n, const char *source_vm_id)
{
	return segment_ok(source_vm_id) ? fmt(out, n, "%s/probes/%s/reply%s", source_vm_id, "") : -EINVAL;
}

/* "a/b/c" 의 index 번째 단계를 [start, end) 로. 없으면 false */
static bool level(const char *s, int index, const char **start, size_t *len)
{
	const char *p = s;

	for (int i = 0; i < index; i++) {
		p = strchr(p, '/');
		if (p == NULL) {
			return false;
		}
		p++;
	}
	const char *end = strchr(p, '/');

	*start = p;
	*len = end ? (size_t)(end - p) : strlen(p);
	return true;
}

static size_t levels(const char *s)
{
	size_t count = 1;

	for (; *s; s++) {
		count += *s == '/';
	}
	return count;
}

static void copy_level(char *out, const char *start, size_t len)
{
	if (len >= XM_ID_MAX) {
		len = 0; /* 너무 긴 id 는 계약 밖으로 본다 */
	}
	memcpy(out, start, len);
	out[len] = '\0';
}

void xm_topic_parse(const char *topic, struct xm_topic_info *info)
{
	const size_t plen = sizeof(XM_PREFIX) - 1;
	const char *seg;
	size_t len;

	info->vm_id[0] = '\0';
	info->app_id[0] = '\0';
	if (topic == NULL || strncmp(topic, XM_PREFIX "/", plen + 1) != 0) {
		return;
	}
	const char *rest = topic + plen + 1;

	/* vms/{vm_id}/{kind} — 정확히 3단계, 각 단계 비어 있지 않음 */
	if (levels(rest) == 3 && strncmp(rest, "vms/", 4) == 0 && level(rest, 1, &seg, &len) && len > 0) {
		const char *kind;
		size_t klen;

		if (level(rest, 2, &kind, &klen) && klen > 0) {
			copy_level(info->vm_id, seg, len);
		}
		return;
	}
	/* apps/{app_id}/events — input 등은 대조 대상이 아니다 */
	if (levels(rest) == 3 && strncmp(rest, "apps/", 5) == 0 && level(rest, 1, &seg, &len) && len > 0) {
		const char *last;
		size_t llen;

		if (level(rest, 2, &last, &llen) && llen == 6 && strncmp(last, "events", 6) == 0) {
			copy_level(info->app_id, seg, len);
		}
	}
}

bool xm_topic_matches(const char *filter, const char *topic)
{
	size_t fl = levels(filter), tl = levels(topic);

	for (size_t i = 0; i < fl; i++) {
		const char *fs, *ts;
		size_t flen, tlen;

		level(filter, (int)i, &fs, &flen);
		if (flen == 1 && fs[0] == '#') {
			return true;
		}
		if (i >= tl) {
			return false;
		}
		level(topic, (int)i, &ts, &tlen);
		if (!(flen == 1 && fs[0] == '+') && (flen != tlen || strncmp(fs, ts, flen) != 0)) {
			return false;
		}
	}
	return fl == tl;
}
