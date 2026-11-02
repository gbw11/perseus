/* 라이브러리 내부용 JSON 도우미 (jsmn 위). 공개 API 가 아니다 */
#ifndef XENOVA_MQTT_XM_INTERNAL_H_
#define XENOVA_MQTT_XM_INTERNAL_H_

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define JSMN_HEADER
#define JSMN_STRICT
#define JSMN_PARENT_LINKS
#include "jsmn.h"

#include "xenova/mqtt/xm.h"

struct xm_json {
	const char *js;
	jsmntok_t tok[XM_JSON_MAX_TOKENS];
	int count;
};

/* 0 성공, -EINVAL 문법 오류, -ENOMEM 토큰 부족 */
int xm_json_parse(struct xm_json *j, const char *js, size_t len);
/* obj(객체 토큰)의 key 값 토큰 index. 같은 key 가 여러 번이면 마지막 (Python json 과 같다). 없으면 -1 */
int xm_json_get(const struct xm_json *j, int obj, const char *key);
/* 토큰 i 와 그 하위를 건너뛴 다음 index */
int xm_json_skip(const struct xm_json *j, int i);

struct xm_span xm_json_span(const struct xm_json *j, int i);
int xm_json_is_string(const struct xm_json *j, int i);
int xm_json_is_object(const struct xm_json *j, int i);
/* JSON 정수(부호·숫자만, true/false·실수 아님)면 0 과 *out */
int xm_json_int(const struct xm_json *j, int i, int64_t *out);
int xm_json_is_null(const struct xm_json *j, int i);
/* JSON 숫자(정수·실수·지수)면 0 과 *out. true/false/null·문자열은 -EINVAL */
int xm_json_number(const struct xm_json *j, int i, double *out);
/* true/false 면 1 과 *out, 아니면 0 */
int xm_json_is_bool(const struct xm_json *j, int i, bool *out);

/* out[*pos..] 에 붙여 쓴다. 넘치면 -ENOSPC 이고 *pos 는 그대로 */
int xm_put(char *out, size_t n, size_t *pos, const char *text);
int xm_put_json_string(char *out, size_t n, size_t *pos, const char *text);
/* 소수 decimals 자리 고정소수점 (Zephyr printf 실수 미지원 대비, 설계 §6 D6) */
int xm_put_fixed(char *out, size_t n, size_t *pos, double value, int decimals);
int xm_put_uint(char *out, size_t n, size_t *pos, uint64_t value);

#endif /* XENOVA_MQTT_XM_INTERNAL_H_ */
