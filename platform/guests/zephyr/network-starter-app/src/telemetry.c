/* SPDX-License-Identifier: Apache-2.0 */
/* Own-domain bootstrap only. Host prepares a read-only, execution-bound node;
 * no identity or secret is compiled into this shared firmware. */
#include <xenova/telemetry.h>
#include <xenova/runtime.h>
#include <zephyr/version.h>
#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

struct bootstrap_read { char own_vm[64],json[XS_VALUE_LIMIT+1]; };

static int read_transaction(struct xs_client *xs,uint32_t tx,int64_t end,void *context)
{
	struct bootstrap_read *value=context;char domid[16],path[128],*tail;
	int rc=xs_read_text(xs,tx,"domid",domid,sizeof(domid),xs_remaining(xs,end));
	if (rc) { return rc; }
	unsigned long id=strtoul(domid,&tail,10);
	if (!domid[0] || *tail || !id || id>0x7fef) { return -EPROTO; }
	(void)snprintf(path,sizeof(path),"/local/domain/%lu/vm",id);
	rc=xs_read_text(xs,tx,path,value->own_vm,sizeof(value->own_vm),xs_remaining(xs,end));
	if (rc) { return rc; }
	(void)snprintf(path,sizeof(path),"/local/domain/%lu/data/xenova-telemetry/bootstrap",id);
	return xs_read_text(xs,tx,path,value->json,sizeof(value->json),xs_remaining(xs,end));
}

static int bootstrap_load(struct xt_bootstrap *out,int64_t end,void *context)
{
	ARG_UNUSED(context);struct bootstrap_read input={0};
	int64_t left=end-k_uptime_get();if (left<=0) { return -ETIMEDOUT; }
	/* main's existing shutdown/runtime initialization owns opening the session.
	 * Avoid xsr_open's independent 1s lock budget inside this 500ms loader. */
	int rc=xsr_transaction((int)left,read_transaction,&input);
	if (!rc) {
		if (strncmp(input.own_vm,"/vm/",4)) { rc=-EACCES; }
		else { rc=xt_bootstrap_decode(input.json,strlen(input.json),input.own_vm+4,out); }
	}
	/* Input is mutable JSON and contains credentials. Never log or shell-print it. */
	for (volatile unsigned char *p=(volatile unsigned char *)&input;p<(volatile unsigned char *)&input+sizeof(input);++p) { *p=0; }
	return rc;
}

int starter_telemetry_start(void)
{
	static const struct xt_inventory info={.os_version=KERNEL_VERSION_STRING,
		.build_id="network-starter-telemetry-2.0",.task_id="diagnostic.exercise",.interface_name="netfront",
#if defined(CONFIG_XENOVA_DIAGNOSTICS_EXERCISE)
		.profile="diagnostic_exercise",
#else
		.profile="network_starter",
#endif
		.diagnostics=true,.publisher=true,.timing=IS_ENABLED(CONFIG_XENOVA_DIAGNOSTICS_TIMING),
		.threads=IS_ENABLED(CONFIG_XENOVA_DIAGNOSTICS_THREADS),.heap=IS_ENABLED(CONFIG_XENOVA_DIAGNOSTICS_HEAP)};
	return xt_start(bootstrap_load,NULL,&info);
}
