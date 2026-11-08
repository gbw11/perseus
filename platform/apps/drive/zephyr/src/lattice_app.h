/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_LATTICE_APP_H
#define XENOVA_LATTICE_APP_H
#include "controller.h"
#include "workers.h"
#include "world.h"
#include <xenova/drive_io.h>
int drive_lattice_start(struct drive_controller *);
void drive_lattice_context(const struct xd_inputs *);
/* Actual worker health/deadline assessment precedes source-state export. */
void drive_lattice_completed(const struct drive_completion *);
/* 실제 completion의 원 clock seq/E/metadata로만 command3를 만든다. */
int drive_lattice_command(const struct drive_completion *,struct xm_drive_command3 *,uint64_t *original_guest_until_us);
/* 송신 시도 bookkeeping이며 실제 STOP/APPLIED 증거는 아니다. */
void drive_lattice_command_attempt(const struct drive_completion *,const struct xm_drive_command3 *);
/* Old AUTO calculation owner has joined before this poll/restart. */
int drive_lattice_reset_poll(int64_t original_deadline_ms);
void drive_lattice_adapter(struct drive_adapter *,bool manual);
int drive_lattice_join(int64_t original_deadline_ms);
/* Called by the state I/O owner. Builds a real completed-path snapshot only;
 * no timer creates calculation progress and no ownership is granted here. */
int drive_lattice_snapshot(dw_state *);
void drive_lattice_report(void);
#endif
