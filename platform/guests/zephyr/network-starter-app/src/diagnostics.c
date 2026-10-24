/* SPDX-License-Identifier: Apache-2.0 */
/* The application adapts netfront counters; Diagnostics Core knows no Xen ABI. */
#include <xenova/diagnostics.h>
#include <xenova/netfront.h>
#include <zephyr/net/net_if.h>
#include <stdatomic.h>
#include "diagnostics_app.h"

static atomic_int startup_rc = -EAGAIN;

static int network_snapshot(struct xdiag_network *out, void *context)
{
	ARG_UNUSED(context);
	int rc = atomic_load(&startup_rc);
	if (rc) { return rc; }
	struct xnf_stats stats;
	rc = xnet_stats(&stats);
	if (rc) { return rc; }
	struct net_if *iface = net_if_get_default();
	if (!iface) { return -ENODEV; }
	*out = (struct xdiag_network){
		.link_up = net_if_is_up(iface) && net_if_is_carrier_ok(iface),
		.tx = stats.tx, .rx = stats.rx, .tx_drop = stats.tx_drop,
		.rx_drop = stats.rx_drop, .pool_busy = stats.pool_busy,
		.protocol_errors = stats.protocol_errors,
	};
	return 0;
}

void starter_diag_boot(const char *stage, int rc) { (void)xdiag_boot(stage, rc); }

void starter_diag_network_result(int rc)
{
	atomic_store(&startup_rc, rc);
	(void)xdiag_network_register(network_snapshot, NULL);
	(void)xdiag_boot("ethernet-start", rc);
}
