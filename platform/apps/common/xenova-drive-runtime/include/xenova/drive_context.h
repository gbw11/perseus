/* SPDX-License-Identifier: Apache-2.0 */
/* Root Bootstrap refresh has its own bounded owner, never the packet path. */
#ifndef XENOVA_DRIVE_CONTEXT_H
#define XENOVA_DRIVE_CONTEXT_H
#include <xenova/telemetry.h>
int xd_context_start(xt_bootstrap_loader);
int xd_context_get(struct xt_bootstrap *);
struct xd_context_status {
 uint64_t loads, failures;
 int first_error, last_error;
 bool available;
};
int xd_context_status_get(struct xd_context_status *);
int xd_context_join(int64_t deadline_ms);
#endif
