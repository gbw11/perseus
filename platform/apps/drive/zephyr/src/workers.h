/* SPDX-License-Identifier: Apache-2.0 */
/* App calculation owners only: no MQTT, authority, Store or restart policy.
 * Context/configuration pointers must outlive a successful worker join. */
#ifndef XENOVA_DRIVE_WORKERS_H
#define XENOVA_DRIVE_WORKERS_H
#include "adapter.h"
#include <xenova/health.h>

enum drive_task { DRIVE_TASK_AUTO, DRIVE_TASK_MANUAL, DRIVE_TASK_COUNT };
enum drive_task_state { DRIVE_TASK_NOT_STARTED, DRIVE_TASK_WAITING, DRIVE_TASK_RUNNING,
 DRIVE_TASK_STOPPING, DRIVE_TASK_STOPPED, DRIVE_TASK_RESIDUAL };
struct drive_job {
 struct xd_manual_witness manual_witness;
 uint64_t sequence, context_revision, connection_revision, release_us, deadline_us, iteration_begin_us;
 uint64_t lifetime;
 int64_t input_received_ms, input_seq, control_seq;
 char vm_id[XM_UUID_LEN + 1];
 char app_instance[XM_UUID_LEN+1];
 size_t control_length, input_length;
 char control[XM_MAX_DRIVING_BYTES + 1], input[XM_MAX_DRIVING_BYTES + 1];
};
struct drive_completion {
 struct drive_job source;
 struct drive_sample sample;
 struct xdh_health health;
 uint64_t sequence, context_revision, progress, begin_us, end_us;
 int rc;
 bool performed;
 int64_t finished_ms;
};
struct drive_task_status {
 enum drive_task_state state;
 uint64_t accepted_sequence, replaced, completed_sequence, lifetime;
 uint64_t running_deadline_us;
 bool started, joined, pending, revoked, joining;
};
/* WAITING is not app.readiness READY. A partial start remains owned;
 * stop/join is required even after failure. */
int drive_workers_start(const struct drive_adapter *automatic, const struct drive_adapter *manual);
/* One newest pending immutable copy per task. No waiting for calculation,
 * free queue slots or completion. Same sequence cannot reissue/widen a job. */
int drive_workers_offer(enum drive_task, const struct drive_job *);
/* A retained completion is not fresh work. Consumers compare sequence and
 * current trusted context; no prior context is accepted on a new revision. */
int drive_workers_read(enum drive_task, uint64_t context_revision, struct drive_completion *);
int drive_workers_status(enum drive_task, struct drive_task_status *);
/* App-local ownership operations. Lifetime is an internal stale-work guard,
 * never permission to mint an MQTT identity. The trusted runtime must approve
 * a new instance before using restarted results outside this owner. */
int drive_task_cancel(enum drive_task, uint64_t expected_lifetime, int64_t deadline_ms);
/* The kernel join runs outside the shared metadata lock. Repeated calls keep
 * the earliest deadline; a late successful call confirms termination only. */
int drive_task_join(enum drive_task, uint64_t expected_lifetime, int64_t deadline_ms);
/* Poll only an already cancelled lifetime. Never renews its stop deadline. */
int drive_task_join_poll(enum drive_task, uint64_t expected_lifetime);
/* Only after actual join/reference release. A retry with the old lifetime
 * returns the same creation result; no second thread or deadline is issued.
 * Adapter context must remain valid and unchanged through the new join. */
int drive_task_restart(enum drive_task, uint64_t expected_lifetime, const struct drive_adapter *fresh);
bool drive_task_stopping(enum drive_task);
/* Close admission first. Every join shares the earliest supplied guest-local
 * uptime-ms deadline. No abort, stack reuse or automatic deadline renewal. */
void drive_workers_stop(void);
/* Future bounded algorithms may check this at their own safe cancel points.
 * Returning from the callback is still required; no thread abort is implied. */
bool drive_workers_stopping(void);
int drive_workers_join(int64_t deadline_ms);
#endif
