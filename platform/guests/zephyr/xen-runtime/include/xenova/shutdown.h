/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_SHUTDOWN_H
#define XENOVA_SHUTDOWN_H
#include <stdint.h>
/* Return 0 only after application quiesce/final state acknowledgement completes.
 * -EBUSY rejects; -ETIMEDOUT and other failures stay failed. No output rearm.
 * Callback runs in its own thread, outside Store/network locks. The deadline is
 * local Zephyr uptime ms, never a timestamp from another machine. */
typedef int (*xshutdown_hook)(const char *request, int64_t deadline, void *context);
int xshutdown_start(xshutdown_hook hook, void *context);
#endif
