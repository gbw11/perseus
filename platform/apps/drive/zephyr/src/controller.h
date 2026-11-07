/* SPDX-License-Identifier: Apache-2.0 */
/* One firmware, two existing calculations. Selection is Gateway-owned; this
 * adapter validates generations and never elects itself or changes algorithms. */
#ifndef XENOVA_DRIVE_CONTROLLER_H
#define XENOVA_DRIVE_CONTROLLER_H
#include "adapter.h"
#include <xenova/health.h>

enum drive_mode { DRIVE_STOP, DRIVE_AUTO, DRIVE_MANUAL };
struct drive_controller {
 struct auto_cfg auto_cfg;
 struct manual_cfg manual_cfg;
 struct drive_adapter automatic,manual;
 struct xm_vehicle_control accepted;
 bool seen_control;
 /* One calculation owner's immutable per-cycle evidence; never MQTT/Store. */
 int (*clock_us)(uint64_t *);
 const struct xdr_snapshot *resources;
 uint64_t deadline_us, resource_uptime_ms;
 int resource_rc;
};
struct drive_result {
 enum drive_mode mode;
 bool control_valid, locally_active;
 uint32_t generation;
 float v_mps, steer_rad;
 const char *auto_state, *manual_state;
 enum auto_reason auto_reason;
 float nearest_m;
 uint64_t auto_progress,manual_progress;
 const char *auto_readiness,*manual_readiness,*auto_not_ready,*manual_not_ready;
 struct xdh_health auto_health,manual_health;
};
/* One coordinator's bounded repeat-attempt metadata, never a delivery ACK. */
struct drive_command_context {
 uint64_t control_revision,connection_revision,lifetime;
 uint32_t generation;
 enum drive_mode mode;
 char app_instance[XM_UUID_LEN+1];
};
struct drive_command_gate {
 struct drive_command_context context;
 uint64_t check;
 char reason[65];
 bool restricted;
};
/* First restricted exact-zero result or changed context/reason is immediate.
 * Same restriction retries at each existing supervisor check. A true return
 * permits an attempt only: mailbox rejection/TX loss is not cached success.
 * Valid results (including neutral zero) and uncacheable inputs always pass. */
bool drive_command_attempt_due(struct drive_command_gate *,const struct drive_command_context *,
 uint64_t check,const char *reason,float v_mps,float steer_rad);
void drive_controller_init(struct drive_controller *);
/* Enable the existing release deadline. Missing resource evidence is advisory;
 * an unverified work clock cannot certify an on-time result. */
void drive_controller_health(struct drive_controller *,int (*clock_us)(uint64_t *),
 uint64_t deadline_us,uint64_t uptime_ms,const struct xdr_snapshot *,int resource_rc);
/* Whole serial step missed the same release deadline, including adapter cost. */
void drive_controller_deadline_missed(struct drive_result *);
/* Pure existing control validation/selection; no app progress or readiness. */
void drive_controller_select(struct drive_controller *, const char *local_vm_id,
 struct xm_span control, struct drive_result *);
/* Both real calculations complete before returning. Missing/invalid/regressed
 * control produces no command; standby still computes the selected mode. */
void drive_controller_step(struct drive_controller *, const char *local_vm_id,
 struct xm_span control, struct xm_span input, int64_t input_age_ms,
 struct xm_span perception, int64_t perception_age_ms, struct drive_result *);
#endif
