/* SPDX-License-Identifier: Apache-2.0 */
/* One system Publisher per guest. MQTT is owned solely by its worker thread. */
#ifndef XENOVA_TELEMETRY_H
#define XENOVA_TELEMETRY_H
#include <xenova/telemetry_core.h>
#include <zephyr/kernel.h>

struct xt_bootstrap {
	struct xt_identity identity;
	char broker_ipv4[16], username[47], password[129];
	uint16_t port;
	/* This transport is explicitly restricted to trusted internal test networks.
	 * Untrusted/TLS-required deployment fails closed, never falls back to TCP. */
	bool trusted_internal_plaintext;
	char hostname[64],display_name[253],artifact_sha256[65],build_id[129];
	bool persistent_crash;
};
typedef int (*xt_bootstrap_loader)(struct xt_bootstrap *, int64_t deadline_ms, void *);
/* Mutable JSON is parsed in place. own_vm_id must come from an independent,
 * verified own-domain source. No credentials may be logged on failure. */
int xt_bootstrap_decode(char *json,size_t bytes,const char *own_vm_id,struct xt_bootstrap *);
/* The trusted Store channel selects the account namespace, never the JSON body. */
int xt_bootstrap_decode_channel(char *json,size_t bytes,const char *own_vm_id,
			       const char *channel,struct xt_bootstrap *);
bool xt_bootstrap_account_matches(const char *username,const char *vm_id,const char *channel);
/* Runtime-owned Store session; NULL selects xenova-telemetry. xenova-drive is
 * a separately provisioned v1 app channel, with separate broker credentials.
 * Never opens another Store receiver or accepts an unbound Xen execution. */
int xt_xen_bootstrap_load(struct xt_bootstrap *, int64_t deadline_ms, void *channel);
int xt_xen_clock_poll(const struct xt_identity *, struct xt_utc *, int64_t deadline_ms);
struct xt_status {
	struct xt_identity identity;
	/* running includes bootstrap-pending worker admission; it is not a join
	 * receipt. identified/connected remain false and last_rc=-EAGAIN initially. */
	bool running, identified, connected;
	int last_rc;
	uint64_t observed_uptime_ms;
	struct xt_counters counters;
};

/* Start never waits for DHCP, credentials or broker. Loader is bounded and must
 * verify own VM identity; it must not print secrets. Missing credentials defer
 * publishing while local Core and application work continue. */
int xt_start(xt_bootstrap_loader, void *context, const struct xt_inventory *);
/* Called by the coordinator before netfront closes; worker closes its own I/O.
 * 0 confirms thread termination, not delivery/vehicle stop. -ETIMEDOUT retains
 * a live/unknown worker; -EBUSY retains API ownership; -EDEADLK rejects self join.
 * Ambiguous socket-close errno is preserved and blocks restart. No flush wait. */
int xt_stop(int64_t deadline_ms);
/* Freeze once in worker; threads=true is an explicit intrusive local request. */
int xt_snapshot_request(bool threads);
int xt_status_get(struct xt_status *);
const char *xt_build_id(void);
/* Worker-only, non-reentrant pinned Zephyr adapter; static scratch is not shared
 * with other callers. Other local readers use their own Core snapshot object. */
int xt_observe(struct xt_observation *, bool threads);
struct shell;
int xt_shell_cmd(const struct shell *, size_t argc, char **argv);
#endif
