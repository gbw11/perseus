/* SPDX-License-Identifier: Apache-2.0 */
/* One firmware, two existing calculations. Selection is Gateway-owned; this
 * adapter validates generations and never elects itself or changes algorithms. */
#ifndef XENOVA_DRIVE_CONTROLLER_H
#define XENOVA_DRIVE_CONTROLLER_H
#include "autonomous.h"
#include "manual.h"

enum drive_mode { DRIVE_STOP, DRIVE_AUTO, DRIVE_MANUAL };
struct drive_controller {
 struct auto_cfg auto_cfg;
 struct manual_cfg manual_cfg;
 struct xm_vehicle_control accepted;
 bool seen_control;
};
struct drive_result {
 enum drive_mode mode;
 bool control_valid, locally_active;
 uint32_t generation;
 float v_mps, steer_rad;
 const char *auto_state, *manual_state;
 enum auto_reason auto_reason;
 float nearest_m;
};
void drive_controller_init(struct drive_controller *);
/* Both real calculations complete before returning. Missing/invalid/regressed
 * control produces no command; standby still computes the selected mode. */
void drive_controller_step(struct drive_controller *, const char *local_vm_id,
 struct xm_span control, struct xm_span input, int64_t input_age_ms,
 struct xm_span perception, int64_t perception_age_ms, struct drive_result *);
#endif
