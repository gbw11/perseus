/* SPDX-License-Identifier: Apache-2.0 */
/* Reuse the runtime's one Store receiver. Each channel has its own host receipt. */
#include <xenova/telemetry.h>
#include <xenova/runtime.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct bootstrap_read {
	const char *channel;
	char own_vm[64], json[XS_VALUE_LIMIT + 1];
};

static int read_transaction(struct xs_client *xs, uint32_t tx, int64_t end, void *context)
{
	struct bootstrap_read *value = context;
	char domid[16], path[128], *tail;
	int rc = xs_read_text(xs, tx, "domid", domid, sizeof(domid), xs_remaining(xs, end));
	if (rc) { return rc; }
	unsigned long id = strtoul(domid, &tail, 10);
	if (!domid[0] || *tail || !id || id > 0x7fef) { return -EPROTO; }
	(void)snprintf(path, sizeof(path), "/local/domain/%lu/vm", id);
	rc = xs_read_text(xs, tx, path, value->own_vm, sizeof(value->own_vm), xs_remaining(xs, end));
	if (rc) { return rc; }
	(void)snprintf(path, sizeof(path), "/local/domain/%lu/data/%s/bootstrap", id, value->channel);
	return xs_read_text(xs, tx, path, value->json, sizeof(value->json), xs_remaining(xs, end));
}

int xt_xen_bootstrap_load(struct xt_bootstrap *out, int64_t end, void *channel)
{
	if (!out) { return -EINVAL; }
	const char *node = channel ? channel : "xenova-telemetry";
	if (strcmp(node, "xenova-telemetry") && strcmp(node, "xenova-drive")) { return -EINVAL; }
	struct bootstrap_read input = {.channel = node};
	int64_t left = end - k_uptime_get();
	if (left <= 0) { return -ETIMEDOUT; }
	int rc = xsr_transaction((int)left, read_transaction, &input);
	if (!rc) {
		rc = strncmp(input.own_vm, "/vm/", 4) ? -EACCES :
			xt_bootstrap_decode(input.json, strlen(input.json), input.own_vm + 4, out);
		/* Empty host/execution binding is allowed only by the native test loader. */
		if (!rc && (!out->identity.host_boot_id[0] || !out->identity.execution_id[0] ||
			    !out->identity.receipt_id[0])) { rc = -EACCES; }
	}
	for (volatile unsigned char *p = (volatile unsigned char *)&input;
	     p < (volatile unsigned char *)&input + sizeof(input); ++p) { *p = 0; }
	if (rc) { memset(out, 0, sizeof(*out)); }
	return rc;
}
