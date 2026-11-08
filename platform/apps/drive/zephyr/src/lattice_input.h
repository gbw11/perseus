/* SPDX-License-Identifier: Apache-2.0 */
/* Binary input owners. No search, vehicle control mutation or serial output. */
#ifndef XENOVA_LATTICE_INPUT_H
#define XENOVA_LATTICE_INPUT_H
#include "engine.h"
#include <xenova/drive_runtime.h>
#include <xenova/drive_context.h>
struct dl_clock {bool acknowledged;int64_t lower,upper;uint64_t expires_guest,expires_pi,probe_sequence;
 char gateway[37],vm[37],execution[37],app_instance[37],host_boot[37];struct dw_trust trust;};
int dl_start(struct drive_engine *);
void dl_context(const struct xd_inputs *);
int dl_clock_get(struct dl_clock *,uint64_t *now_pi_upper);
int dl_manual_clock_get(struct dl_clock *,uint64_t *now_pi_upper);
/* 실제 fresh Reply의 현재 owner witness만 반환한다. StateCopy gate는 입력이 아니다. */
int dl_direction_get(bool manual,xenova_drive_DirectionGateWitness *,uint64_t now_pi_us);
/* 수신한 원 receipt만 반환한다. 이 호출/예산 결과는 출력 권위가 아니다. */
int dl_mission_get(xenova_drive_MissionGrant *,uint64_t now_pi_us);
int dl_mission_for_local(xenova_drive_MissionGrant *,uint64_t now_pi_us);
int dl_budget_submit(const xenova_drive_MissionBudgetRequest *,const uint8_t canonical_request_sha256[32]);
int dl_budget_take(xenova_drive_MissionBudgetRequest *,xenova_drive_MissionBudgetReply *);
/* Atomic ownership transfer of a complete accepted copy to the planner owner. */
int dl_state_take(const dw_state **);
void dl_state_release(int slot);
int dl_applied(const xenova_drive_AppliedState *);
int dl_join(int64_t deadline_ms);
const char *dl_status(void);
/* Call only after input owners joined; no periodic console sink. */
void dl_report(void);
#endif
