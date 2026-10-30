/* SPDX-License-Identifier: Apache-2.0 */
/* Local status/explicit snapshot only. No credentials or remote commands. */
#include <xenova/telemetry.h>
#include <zephyr/shell/shell.h>
#include <inttypes.h>
#include <string.h>
int xt_shell_cmd(const struct shell *shell,size_t argc,char **argv)
{
	if (argc==2 && (!strcmp(argv[1],"threads") || !strcmp(argv[1],"history"))) {
		return xt_snapshot_request(!strcmp(argv[1],"threads"));
	}
	if (argc!=1) { return -EINVAL; }
	struct xt_status s;int rc=xt_status_get(&s);if (rc) { return rc; }
	shell_print(shell,"{\"running\":%s,\"identified\":%s,\"connected\":%s,\"last_rc\":%d,\"observed_uptime_ms\":\"%" PRIu64 "\",\"submitted\":\"%" PRIu64 "\",\"pubacked\":\"%" PRIu64 "\",\"source_missed\":\"%" PRIu64 "\",\"events_dropped\":\"%" PRIu64 "\",\"live_discarded\":\"%" PRIu64 "\",\"snapshots_expired\":\"%" PRIu64 "\",\"collect_us\":\"%" PRIu64 "\",\"serialize_us\":\"%" PRIu64 "\",\"send_us\":\"%" PRIu64 "\"}",
	 s.running ? "true" : "false",s.identified ? "true" : "false",s.connected ? "true" : "false",s.last_rc,s.observed_uptime_ms,s.counters.submitted,s.counters.pubacked,s.counters.source_missed,s.counters.events_dropped,s.counters.live_discarded,s.counters.snapshots_expired,s.counters.collect_us,s.counters.serialized_us,s.counters.send_us);
	return 0;
}
