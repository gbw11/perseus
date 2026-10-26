/* SPDX-License-Identifier: Apache-2.0 */
/* App replacement boundary: calculation, input/state readiness and output
 * validation belong here. The runtime does not interpret algorithm reasons. */
#ifndef XENOVA_DRIVE_ADAPTER_H
#define XENOVA_DRIVE_ADAPTER_H
#include <xenova/mqtt/xm.h>
#include "autonomous.h"
#include "manual.h"

struct drive_sample { float v_mps,steer_rad,nearest_m; enum auto_reason auto_reason; const char *reason; };
typedef int (*drive_calculate)(void *context,struct xm_span control,struct xm_span input,
                               int64_t age_ms,struct drive_sample *out);
struct drive_adapter {
 drive_calculate calculate;
 void *context;
 uint32_t input_timeout_ms;
 uint64_t progress;
 /* Stateful replacements must leave state_ready false until their own
  * synchronization/warm-up completes; a return from calculate is insufficient. */
 bool requires_state_sync,state_ready;
};
void drive_auto_adapter(struct drive_adapter *,struct auto_cfg *);
void drive_manual_adapter(struct drive_adapter *,struct manual_cfg *);
int drive_adapter_run(struct drive_adapter *,struct xm_span control,struct xm_span input,
                      int64_t age_ms,struct drive_sample *out);
#endif
