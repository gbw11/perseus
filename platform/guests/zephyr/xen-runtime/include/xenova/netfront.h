/* SPDX-License-Identifier: Apache-2.0 */
#ifndef XENOVA_NETFRONT_H
#define XENOVA_NETFRONT_H
#include <xenova/netfront_ring.h>
#include <stdint.h>
int xnet_start(void);
int xnet_close(int64_t deadline);
int xnet_stats(struct xnf_stats *);
#endif
