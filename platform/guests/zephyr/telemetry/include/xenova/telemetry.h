/* SPDX-License-Identifier: Apache-2.0 */
/* One system Publisher per guest. MQTT is owned solely by its worker thread. */
#ifndef XENOVA_TELEMETRY_H
#define XENOVA_TELEMETRY_H
#include <xenova/telemetry_core.h>
#include <zephyr/kernel.h>

struct xt_bootstrap {
	struct xt_identity identity;
	char broker_ipv4[16], username[37], password[129];
	uint16_t port;
	/* This transport is explicitly restricted to trusted internal test networks.
	 * Untrusted/TLS-required deployment fails closed, never falls back to TCP. */
	bool trusted_internal_plaintext;
};
typedef int (*xt_bootstrap_loader)(struct xt_bootstrap *, int64_t deadline_ms, void *);
/* Mutable JSON is parsed in place. own_vm_id must come from an independent,
 * verified own-domain source. No credentials may be logged on failure. */
int xt_bootstrap_decode(char *json,size_t bytes,const char *own_vm_id,struct xt_bootstrap *);
struct xt_status {
	struct xt_identity identity;
	bool running, identified, connected;
	int last_rc;
	uint64_t observed_uptime_ms;
	struct xt_counters counters;
};

/* Start never waits for DHCP, credentials or broker. Loader is bounded and must
 * verify own VM identity; it must not print secrets. Missing credentials defer
 * publishing while local Core and application work continue. */
int xt_start(xt_bootstrap_loader, void *context, const struct xt_inventory *);
/* Called by existing quiesce before netfront closes. No unbounded flush. */
int xt_stop(int64_t deadline_ms);
/* Freeze once in worker; threads=true is an explicit intrusive local request. */
int xt_snapshot_request(bool threads);
int xt_status_get(struct xt_status *);
/* Worker-only, non-reentrant pinned Zephyr adapter; static scratch is not shared
 * with other callers. Other local readers use their own Core snapshot object. */
int xt_observe(struct xt_observation *, bool threads);
struct shell;
int xt_shell_cmd(const struct shell *, size_t argc, char **argv);
#endif
