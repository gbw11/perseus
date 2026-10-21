/* 발행자·토픽별 마지막 seq — Python envelope.SeqTracker 와 같다. 고정 슬롯, 가득 차면 가장 오래 안 쓴 슬롯을 바꾼다 */
#include <string.h>

#include "xenova/mqtt/xm.h"

void xm_seq_init(struct xm_seq_tracker *t)
{
	memset(t, 0, sizeof(*t));
}

static bool fits(struct xm_span s, size_t cap)
{
	return (size_t)s.len < cap;
}

bool xm_seq_accept(struct xm_seq_tracker *t, const char *topic, const struct xm_envelope *env)
{
	if (env->seq < 0) {
		return true; /* MQTT-04 명령처럼 seq 가 없는 봉투 */
	}
	struct xm_span who = env->vm_id.p ? env->vm_id : env->source_id;
	char publisher[XM_ID_MAX], run[sizeof(t->slots[0].run)];

	if (strlen(topic) >= XM_TOPIC_MAX || !fits(who, sizeof(publisher)) ||
	    (size_t)env->boot_id.len + env->instance_id.len + 2 > sizeof(run)) {
		return true; /* 슬롯에 담을 수 없는 키는 추적하지 않는다 (계약 밖 크기) */
	}
	memcpy(publisher, who.p ? who.p : "", who.len);
	publisher[who.len] = '\0';
	memcpy(run, env->boot_id.p, env->boot_id.len);
	run[env->boot_id.len] = '/';
	memcpy(run + env->boot_id.len + 1, env->instance_id.p, env->instance_id.len);
	run[env->boot_id.len + 1 + env->instance_id.len] = '\0';

	struct xm_seq_slot *slot = NULL, *victim = &t->slots[0];

	t->clock++;
	for (int i = 0; i < XM_SEQ_SLOTS; i++) {
		struct xm_seq_slot *s = &t->slots[i];

		if (s->used && strcmp(s->topic, topic) == 0 && strcmp(s->publisher, publisher) == 0) {
			slot = s;
			break;
		}
		if (s->used < victim->used) {
			victim = s; /* 빈 슬롯(used 0)이 가장 먼저 뽑힌다 */
		}
	}
	if (slot != NULL && strcmp(slot->run, run) == 0 && env->seq <= slot->last) {
		slot->used = t->clock;
		return false;
	}
	if (slot == NULL) {
		slot = victim;
		strcpy(slot->topic, topic);
		strcpy(slot->publisher, publisher);
	}
	strcpy(slot->run, run);
	slot->last = env->seq;
	slot->used = t->clock;
	return true;
}
