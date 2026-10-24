/* jsmn 토큰 위의 작은 JSON 도우미와 출력 버퍼 쓰기 */
#include <errno.h>
#include <string.h>

/* jsmn 구현은 이 파일 하나에만 넣는다 */
#undef JSMN_HEADER
#define JSMN_STRICT
#define JSMN_PARENT_LINKS
#include "jsmn.h"
#define JSMN_HEADER

#include "xm_internal.h"

int xm_json_parse(struct xm_json *j, const char *js, size_t len)
{
	jsmn_parser parser;

	jsmn_init(&parser);
	j->js = js;
	j->count = jsmn_parse(&parser, js, len, j->tok, XM_JSON_MAX_TOKENS);
	if (j->count == JSMN_ERROR_NOMEM) {
		return -ENOMEM;
	}
	if (j->count < 1) {
		return -EINVAL;
	}
	/* jsmn 은 최상위 값 뒤의 꼬리를 허용하므로 공백 말고 남은 게 있으면 거부 (Python json.loads 와 같게) */
	for (size_t k = (size_t)j->tok[0].end; k < len; k++) {
		if (js[k] != ' ' && js[k] != '\t' && js[k] != '\n' && js[k] != '\r') {
			return -EINVAL;
		}
	}
	return 0;
}

int xm_json_skip(const struct xm_json *j, int i)
{
	int end = j->tok[i].end;

	i++;
	while (i < j->count && j->tok[i].start < end) {
		i++;
	}
	return i;
}

int xm_json_get(const struct xm_json *j, int obj, const char *key)
{
	int found = -1;
	size_t klen = strlen(key);

	if (j->tok[obj].type != JSMN_OBJECT) {
		return -1;
	}
	int i = obj + 1;

	for (int pair = 0; pair < j->tok[obj].size && i < j->count; pair++) {
		const jsmntok_t *k = &j->tok[i];

		if (k->type == JSMN_STRING && (size_t)(k->end - k->start) == klen &&
		    memcmp(j->js + k->start, key, klen) == 0 && i + 1 < j->count) {
			found = i + 1;
		}
		i = xm_json_skip(j, i + 1); /* 키 다음 값 건너뛰기 */
	}
	return found;
}

struct xm_span xm_json_span(const struct xm_json *j, int i)
{
	struct xm_span s = {NULL, 0};

	if (i >= 0 && i < j->count) {
		s.p = j->js + j->tok[i].start;
		s.len = (uint16_t)(j->tok[i].end - j->tok[i].start);
	}
	return s;
}

int xm_json_is_string(const struct xm_json *j, int i)
{
	return i >= 0 && j->tok[i].type == JSMN_STRING;
}

int xm_json_is_object(const struct xm_json *j, int i)
{
	return i >= 0 && j->tok[i].type == JSMN_OBJECT;
}

int xm_json_is_null(const struct xm_json *j, int i)
{
	return i >= 0 && j->tok[i].type == JSMN_PRIMITIVE && j->tok[i].end - j->tok[i].start == 4 &&
	       memcmp(j->js + j->tok[i].start, "null", 4) == 0;
}

int xm_json_int(const struct xm_json *j, int i, int64_t *out)
{
	if (i < 0 || j->tok[i].type != JSMN_PRIMITIVE) {
		return -EINVAL;
	}
	const char *p = j->js + j->tok[i].start, *end = j->js + j->tok[i].end;
	bool neg = false;
	int64_t v = 0;

	if (p < end && *p == '-') {
		neg = true;
		p++;
	}
	if (p == end) {
		return -EINVAL;
	}
	for (; p < end; p++) {
		if (*p < '0' || *p > '9') {
			return -EINVAL; /* true/false/null, 실수(1.0, 1e3) 는 정수가 아니다 */
		}
		if (v > (INT64_MAX - 9) / 10) {
			return -ERANGE;
		}
		v = v * 10 + (*p - '0');
	}
	*out = neg ? -v : v;
	return 0;
}

int xm_json_number(const struct xm_json *j, int i, double *out)
{
	if (i < 0 || j->tok[i].type != JSMN_PRIMITIVE) {
		return -EINVAL;
	}
	const char *p = j->js + j->tok[i].start, *end = j->js + j->tok[i].end;
	double v = 0.0, scale = 1.0;
	int exp = 0, digits = 0;
	bool neg = false;

	/* strtod 대신 직접 읽는다: 토큰 밖을 읽지 않고, Zephyr libc 설정에 기대지 않는다 */
	if (p < end && *p == '-') {
		neg = true;
		p++;
	}
	for (; p < end && *p >= '0' && *p <= '9'; p++, digits++) {
		v = v * 10.0 + (*p - '0');
	}
	if (p < end && *p == '.') {
		for (p++; p < end && *p >= '0' && *p <= '9'; p++, digits++) {
			scale /= 10.0;
			v += (*p - '0') * scale;
		}
	}
	if (digits == 0) {
		return -EINVAL; /* true/false/null */
	}
	if (p < end && (*p == 'e' || *p == 'E')) {
		bool eneg = false;
		int edigits = 0;

		p++;
		if (p < end && (*p == '+' || *p == '-')) {
			eneg = *p == '-';
			p++;
		}
		for (; p < end && *p >= '0' && *p <= '9'; p++, edigits++) {
			if (exp < 400) {
				exp = exp * 10 + (*p - '0');
			}
		}
		if (edigits == 0) {
			return -EINVAL;
		}
		for (int k = 0; k < exp && v != 0.0; k++) {
			v = eneg ? v / 10.0 : v * 10.0;
		}
	}
	if (p != end) {
		return -EINVAL;
	}
	*out = neg ? -v : v;
	return 0;
}

int xm_json_is_bool(const struct xm_json *j, int i, bool *out)
{
	if (i < 0 || j->tok[i].type != JSMN_PRIMITIVE) {
		return 0;
	}
	const char *p = j->js + j->tok[i].start;
	int len = j->tok[i].end - j->tok[i].start;

	if (len == 4 && memcmp(p, "true", 4) == 0) {
		*out = true;
		return 1;
	}
	if (len == 5 && memcmp(p, "false", 5) == 0) {
		*out = false;
		return 1;
	}
	return 0;
}

bool xm_span_eq(struct xm_span s, const char *text)
{
	size_t n = strlen(text);

	return s.p != NULL && s.len == n && memcmp(s.p, text, n) == 0;
}

int xm_span_copy(char *out, size_t n, struct xm_span s)
{
	if (s.p == NULL || (size_t)s.len + 1 > n) {
		return -ENOSPC;
	}
	memcpy(out, s.p, s.len);
	out[s.len] = '\0';
	return s.len;
}

int xm_put(char *out, size_t n, size_t *pos, const char *text)
{
	size_t len = strlen(text);

	if (*pos + len + 1 > n) {
		return -ENOSPC;
	}
	memcpy(out + *pos, text, len + 1);
	*pos += len;
	return 0;
}

int xm_put_json_string(char *out, size_t n, size_t *pos, const char *text)
{
	static const char hex[] = "0123456789abcdef";
	size_t p = *pos;

#define PUTC(c)                                                                                                \
	do {                                                                                                   \
		if (p + 2 > n) {                                                                               \
			return -ENOSPC;                                                                        \
		}                                                                                              \
		out[p++] = (c);                                                                                \
	} while (0)

	PUTC('"');
	for (const unsigned char *s = (const unsigned char *)text; *s; s++) {
		if (*s == '"' || *s == '\\') {
			PUTC('\\');
			PUTC((char)*s);
		} else if (*s < 0x20) {
			PUTC('\\');
			PUTC('u');
			PUTC('0');
			PUTC('0');
			PUTC(hex[*s >> 4]);
			PUTC(hex[*s & 0xf]);
		} else {
			PUTC((char)*s);
		}
	}
	PUTC('"');
#undef PUTC
	out[p] = '\0';
	*pos = p;
	return 0;
}

int xm_put_uint(char *out, size_t n, size_t *pos, uint64_t value)
{
	char buf[21];
	int i = (int)sizeof(buf) - 1;

	buf[i] = '\0';
	do {
		buf[--i] = (char)('0' + value % 10);
		value /= 10;
	} while (value);
	return xm_put(out, n, pos, buf + i);
}

int xm_put_fixed(char *out, size_t n, size_t *pos, double value, int decimals)
{
	size_t start = *pos;
	uint64_t scale = 1;

	/* libm 없이 (Zephyr minimal libc). NaN·Inf 는 JSON 에 없으므로 0, 너무 큰 값은 자른다. 주행 값이면 정지 쪽 */
	if (value != value || value - value != 0.0) {
		value = 0.0;
	}
	if (value > 1e12) {
		value = 1e12;
	} else if (value < -1e12) {
		value = -1e12;
	}
	for (int d = 0; d < decimals; d++) {
		scale *= 10;
	}
	bool neg = value < 0;
	double magnitude = neg ? -value : value;
	uint64_t scaled = (uint64_t)(magnitude * (double)scale + 0.5);
	uint64_t ip = scaled / scale, fp = scaled % scale;
	int rc = 0;

	if (neg && scaled != 0) {
		rc |= xm_put(out, n, pos, "-");
	}
	rc |= xm_put_uint(out, n, pos, ip);
	if (decimals > 0) {
		char frac[24];

		frac[0] = '.';
		for (int d = decimals; d > 0; d--) {
			frac[d] = (char)('0' + fp % 10);
			fp /= 10;
		}
		frac[decimals + 1] = '\0';
		rc |= xm_put(out, n, pos, frac);
	}
	if (rc) {
		*pos = start;
		out[start] = '\0';
		return -ENOSPC;
	}
	return 0;
}
