/* SPDX-License-Identifier: Apache-2.0 */
/* Decode trusted per-instance injection. This does not establish its provenance. */
#include <xenova/telemetry.h>
#include <zephyr/data/json.h>
#include <errno.h>
#include <string.h>
struct raw_bootstrap {
	char *vm_id,*boot_id,*publisher_id,*host_boot_id,*execution_id,*receipt_id;
	char *broker_ipv4,*username,*password;
	int port;
	bool trusted_internal_plaintext;
};
#define FIELD(name) JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,name,JSON_TOK_STRING)
static const struct json_obj_descr fields[]={
	FIELD(vm_id),FIELD(boot_id),FIELD(publisher_id),FIELD(host_boot_id),FIELD(execution_id),FIELD(receipt_id),
	FIELD(broker_ipv4),FIELD(username),FIELD(password),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,port,JSON_TOK_NUMBER),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,trusted_internal_plaintext,JSON_TOK_TRUE)
};
#undef FIELD
static int copy(char *out,size_t capacity,const char *in)
{
	if (!in || strlen(in)>=capacity) { return -EOVERFLOW; }
	memcpy(out,in,strlen(in)+1);return 0;
}
int xt_bootstrap_decode(char *json,size_t bytes,const char *own_vm_id,struct xt_bootstrap *out)
{
	if (!json || !out || !xt_uuid_valid(own_vm_id)) { return -EINVAL; }
	memset(out,0,sizeof(*out));struct raw_bootstrap raw={0};
	int64_t found=json_obj_parse(json,bytes,fields,ARRAY_SIZE(fields),&raw);
	if (found!=((1LL<<ARRAY_SIZE(fields))-1)) { return -EPROTO; }
	if (strcmp(own_vm_id,raw.vm_id) || raw.port<1 || raw.port>65535 || !raw.password[0] ||
	    strcmp(raw.username,own_vm_id) || !raw.trusted_internal_plaintext) { return -EACCES; }
	if (!xt_uuid_valid(raw.boot_id) || !xt_uuid_valid(raw.publisher_id)) { return -EINVAL; }
	bool bound=raw.host_boot_id[0] || raw.execution_id[0] || raw.receipt_id[0];
	if (bound && (!xt_uuid_valid(raw.host_boot_id) || !xt_uuid_valid(raw.execution_id) || !xt_uuid_valid(raw.receipt_id))) { return -EINVAL; }
	int rc;
#define COPY(dst,src) do { rc=copy(dst,sizeof(dst),src);if (rc) { memset(out,0,sizeof(*out));return rc; } } while (0)
	COPY(out->identity.vm_id,raw.vm_id);COPY(out->identity.boot_id,raw.boot_id);
	COPY(out->identity.publisher_id,raw.publisher_id);COPY(out->identity.host_boot_id,raw.host_boot_id);
	COPY(out->identity.execution_id,raw.execution_id);COPY(out->identity.receipt_id,raw.receipt_id);
	COPY(out->broker_ipv4,raw.broker_ipv4);COPY(out->username,raw.username);COPY(out->password,raw.password);
#undef COPY
	out->port=(uint16_t)raw.port;out->trusted_internal_plaintext=true;return 0;
}
