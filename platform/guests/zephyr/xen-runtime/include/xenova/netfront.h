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
};
int xnet_status_get(struct xnet_status *);
int xnet_stats(struct xnf_stats *);
#endif
