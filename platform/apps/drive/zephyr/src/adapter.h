/* SPDX-License-Identifier: Apache-2.0 */
/* App replacement boundary: calculation, input/state readiness and output
 * validation belong here. The runtime does not interpret algorithm reasons. */
#ifndef XENOVA_DRIVE_ADAPTER_H
#define XENOVA_DRIVE_ADAPTER_H
#include <xenova/mqtt/xm.h>
#include "autonomous.h"
#include "manual.h"
#include "preparation.h"
#include "manual_session.h"
#include "state_transfer.h"
#include <xenova/manual_input.h>

struct drive_sample { float v_mps,steer_rad,nearest_m; enum auto_reason auto_reason; const char *reason; };
typedef int (*drive_calculate)(void *context,struct xm_span control,struct xm_span input,
                               int64_t age_ms,struct drive_sample *out);
/* Bounded validation of immutable configuration/input, no work progress or I/O. */
typedef const char *(*drive_qualify)(const void *context,struct xm_span input,int64_t age_ms);
struct drive_adapter {
 drive_calculate calculate;
 void *context;
 uint32_t input_timeout_ms;
 uint64_t progress;
 /* Stateful replacements must leave state_ready false until their own
  * synchronization/warm-up completes; a return from calculate is insufficient. */
 bool requires_state_sync,state_ready;
 /* Immutable app-owned proof and expected receiving context. Both outlive
  * worker join. A boolean alone never qualifies a stateful replacement. */
 const struct drive_state_preparation *prepared_state;
 const struct drive_state_context *state_context;
 bool manual_input;
 struct drive_manual_gate movement;
 drive_qualify qualify;
};
/* Successful application is preparation, not calculation progress or READY.
 * The adapter owner must already have joined any previous state consumer. */
static inline int drive_adapter_apply_maneuver(struct drive_adapter *adapter,
 struct drive_maneuver_copy *copy,const struct xm_envelope *envelope,struct xm_span payload,
 const struct xm_vehicle_control *control,const char *receiver_vm,
 const struct drive_state_context *target,const struct xm_state_context *current,
 const char *const *segments,size_t count,uint64_t now,uint64_t deadline)
{
 if(!adapter||!adapter->requires_state_sync||adapter->state_ready)return -EINVAL;
 int rc=drive_apply_maneuver(copy,envelope,payload,control,receiver_vm,target,current,segments,count,now,deadline);
 if(!rc){adapter->prepared_state=&copy->prepared;adapter->state_context=target;adapter->state_ready=true;}
 return rc;
}
void drive_auto_adapter(struct drive_adapter *,struct auto_cfg *);
void drive_manual_adapter(struct drive_adapter *,struct manual_cfg *);
int drive_adapter_run(struct drive_adapter *,struct xm_span control,struct xm_span input,
                      int64_t age_ms,struct drive_sample *out);
/* Immutable receive-owner neutral proof is scoped to this app instance.
 * It does not grant movement; the latest fresh sample must still pass the gate. */
int drive_adapter_run_observed(struct drive_adapter *,struct xm_span control,struct xm_span input,
 int64_t age_ms,const struct xd_manual_witness *,const char *receiver,struct drive_sample *out);
const char *drive_adapter_qualify(const struct drive_adapter *,struct xm_span input,int64_t age_ms);
#endif
