/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_NETFRONT_H
#define XENOVA_NETFRONT_H
#include <xenova/netfront_ring.h>
#include <stdint.h>
int xnet_start(void);
int xnet_close(int64_t deadline);
/* Cancels future initialization/TX; does not free resources or wait. A call
 * already admitted by the initializer may finish and is owned until close. */
void xnet_stop_request(void);
struct xnet_status {
 bool started, stopping, ring_ready, worker_joined, watch_owned;
 bool publication_possible, interface_possible, closed;
 unsigned grants_held;
 /* Preserve first startup/cleanup failure independently of current closed. */
 int event_port, initial_error, cleanup_error;
 const char *stage;
 bool monitor_started, monitor_joined;
 /* Last observation: 0=not observed, 1=confirmed Connected, 2=unavailable,
  * 3=backend closing/closed, 4=identity/protocol/transport failure.
  * backend_error is terminal only. Observation recovery never reconnects or
  * reinitializes resources. connected is TX admission, not application health. */
 int backend_observation, backend_error;
 int observation_error, first_observation_error;
 bool connected;
 int backend_wait_stage; /* 0=none/complete, 1=session acquire, 2=response */
};
struct xnet_waits {
 uint64_t packet_polls, pending_batches, packet_gap_max_us;
 uint64_t packet_lock_wait_max_us, packet_lock_hold_max_us;
 uint64_t store_observations, store_lock_wait_max_us, store_response_max_us;
 uint64_t store_unavailable, store_recoveries, last_backend_success_ms;
 bool cycle_clock; /* false: kernel-tick granularity, not sub-tick precision */
};
int xnet_waits_get(struct xnet_waits *);
int xnet_status_get(struct xnet_status *);
int xnet_stats(struct xnf_stats *);
/* Low-cost cache collector: never blocks the system workqueue on packet I/O. */
int xnet_stats_try(struct xnf_stats *);
#endif
