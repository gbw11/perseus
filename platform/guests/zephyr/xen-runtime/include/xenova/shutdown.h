/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_SHUTDOWN_H
#define XENOVA_SHUTDOWN_H
#include <stdint.h>
#include <stdbool.h>
/* Return 0 only after application quiesce/final state acknowledgement completes.
 * -EBUSY rejects; -ETIMEDOUT and other failures stay failed. No output rearm.
 * Callback runs in its own thread, outside Store/network locks. The deadline is
 * local Zephyr uptime ms, never a timestamp from another machine. */
typedef int (*xshutdown_hook)(const char *request, int64_t deadline, void *context);
int xshutdown_start(xshutdown_hook hook, void *context);
/* Local initialization unwind; never submits a Xen shutdown. -EBUSY means
 * an accepted external request owns cleanup, including its failure. */
int xshutdown_stop(int64_t deadline);
struct xshutdown_status {
 bool started, worker_joined, watch_owned, features_possible, request_owned;
 int initial_error, cleanup_error;
};
int xshutdown_status_get(struct xshutdown_status *out);
#endif
