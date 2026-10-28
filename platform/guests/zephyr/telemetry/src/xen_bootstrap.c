/* SPDX-License-Identifier: Apache-2.0 */
/* Reuse the runtime's one Store receiver. Each channel has its own host receipt. */
#include <xenova/telemetry.h>
#include <xenova/runtime.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/data/json.h>
#include <inttypes.h>

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

struct clock_exchange { const struct xt_identity *id; char request[160],response[768]; bool write; };
static int clock_transaction(struct xs_client *xs,uint32_t tx,int64_t end,void *context)
{
 struct clock_exchange *e=context;char domid[16],path[128],vm[64];
 int rc=xs_read_text(xs,tx,"domid",domid,sizeof(domid),xs_remaining(xs,end));if(rc)return rc;
 for(const char *p=domid;*p;p++)if(*p<'0'||*p>'9')return -EPROTO;
 snprintf(path,sizeof(path),"/local/domain/%s/vm",domid);
 rc=xs_read_text(xs,tx,path,vm,sizeof(vm),xs_remaining(xs,end));if(rc)return rc;
 if(strncmp(vm,"/vm/",4)||strcmp(vm+4,e->id->vm_id))return -EACCES;
 snprintf(path,sizeof(path),"/local/domain/%s/data/xenova-telemetry/clock/%s",domid,e->write?"request":"response");
 return e->write?xs_write(xs,tx,path,e->request,strlen(e->request),xs_remaining(xs,end)):
  xs_read_text(xs,tx,path,e->response,sizeof(e->response),xs_remaining(xs,end));
}
int xt_xen_clock_poll(const struct xt_identity *id,struct xt_utc *clock,int64_t deadline)
{
 static uint64_t requested,next_request;static char expected[37];
 uint64_t now=k_uptime_get();
 if(strcmp(expected,id->execution_id)){strcpy(expected,id->execution_id);requested=next_request=0;clock->valid=false;}
 if(requested&&now-requested>5000){requested=0;clock->valid=false;}
 struct clock_exchange e={.id=id};
 if(!requested){
  if(now<next_request)return 0;
  requested=now?now:1;next_request=now+10000;e.write=true;
  snprintf(e.request,sizeof(e.request),"{\"nonce\":\"%" PRIu64 "\",\"execution_id\":\"%s\"}",requested,id->execution_id);
 }
 int64_t left=deadline-k_uptime_get();if(left<=0)return -ETIMEDOUT;
 int rc=xsr_transaction((int)left,clock_transaction,&e);if(rc){clock->valid=false;return rc;}
 if(e.write)return -EAGAIN;
 struct response {char *nonce,*host_boot_id,*execution_id,*unix_ms,*source;int source_error_ms,drift_ppm;} raw={0};
#define F(n,t) JSON_OBJ_DESCR_PRIM(struct response,n,t)
 static const struct json_obj_descr fields[]={F(nonce,JSON_TOK_STRING),F(host_boot_id,JSON_TOK_STRING),
  F(execution_id,JSON_TOK_STRING),F(unix_ms,JSON_TOK_STRING),F(source,JSON_TOK_STRING),
  F(source_error_ms,JSON_TOK_NUMBER),F(drift_ppm,JSON_TOK_NUMBER)};
#undef F
 if(json_obj_parse(e.response,strlen(e.response),fields,ARRAY_SIZE(fields),&raw)!=127)return -EAGAIN;
 char *tail;uint64_t nonce=strtoull(raw.nonce,&tail,10);
 if(*tail||nonce!=requested)return -EAGAIN;
 if(strcmp(raw.host_boot_id,id->host_boot_id)||strcmp(raw.execution_id,id->execution_id)){clock->valid=false;return -EXDEV;}
 uint64_t utc=strtoull(raw.unix_ms,&tail,10);if(*tail){clock->valid=false;return -EPROTO;}
 rc=xt_utc_update(clock,requested,k_uptime_get(),utc,raw.source_error_ms,raw.drift_ppm,raw.source);
 requested=0;return rc;
}
