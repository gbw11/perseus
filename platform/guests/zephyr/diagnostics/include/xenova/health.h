/* SPDX-License-Identifier: Apache-2.0 */
/* Local function-health evidence, not VM recovery or output authority. The
 * caller owns task lifetime and supplies its existing release deadline. */
#ifndef XENOVA_HEALTH_H
#define XENOVA_HEALTH_H
#include <xenova/resources.h>
enum xdh_work_state { XDH_NOT_STARTED, XDH_IDLE, XDH_RUNNING, XDH_COMPLETED, XDH_STOPPING };
enum xdh_failure { XDH_NONE, XDH_WORK_FAILED, XDH_RESOURCE_FAILED, XDH_DEADLINE, XDH_UNVERIFIED };
enum xdh_warning {
 XDH_CPU_UNAVAILABLE=1u, XDH_CPU_NO_IDLE=2u, XDH_MEMORY_UNAVAILABLE=4u,
 XDH_MEMORY_NO_FREE=8u, XDH_THREADS_INCOMPLETE=16u
};
struct xdh_work {
 enum xdh_work_state state;
 bool required;
 int rc, clock_rc;
 uint64_t begin_us, end_us, deadline_us;
};
struct xdh_health {
 enum xdh_failure failure;
 uint32_t warnings;
 int work_rc, cpu_rc;
 bool restrict_function, cpu_valid;
 uint32_t cpu_percent_milli;
 uint64_t resource_sample, cpu_start_ms, cpu_end_ms;
};
/* now/work use one guest-local clock; resources use guest uptime milliseconds.
 * Resource gaps are advisory, not a new v2 delivery requirement. Completed
 * work exactly at its deadline is on time. No I/O, scan, allocation or lock. */
int xdh_assess(const struct xdh_work *, uint64_t now_us, uint64_t uptime_ms,
               const struct xdr_snapshot *, int resource_rc, struct xdh_health *);
#endif
