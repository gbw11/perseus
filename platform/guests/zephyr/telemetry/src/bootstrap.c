/* SPDX-License-Identifier: Apache-2.0 */
/* Decode trusted per-instance injection. This does not establish its provenance. */
#include <xenova/telemetry.h>
#include <zephyr/data/json.h>
#include <errno.h>
#include <string.h>
#include "writer.h"
struct raw_bootstrap {
	char *vm_id,*boot_id,*publisher_id,*host_boot_id,*execution_id,*receipt_id;
	char *broker_ipv4,*username,*password;
	char hostname[64],display_name[253],artifact_sha256[65],build_id[129];
	bool persistent_crash;
	int port;
	bool trusted_internal_plaintext;
	char drive_profile[24],auto_instance_id[37],manual_instance_id[37];
};
#define FIELD(name) JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,name,JSON_TOK_STRING)
static const struct json_obj_descr fields[]={
	FIELD(vm_id),FIELD(boot_id),FIELD(publisher_id),FIELD(host_boot_id),FIELD(execution_id),FIELD(receipt_id),
	FIELD(broker_ipv4),FIELD(username),FIELD(password),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,port,JSON_TOK_NUMBER),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,trusted_internal_plaintext,JSON_TOK_TRUE),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,hostname,JSON_TOK_STRING_BUF),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,display_name,JSON_TOK_STRING_BUF),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,artifact_sha256,JSON_TOK_STRING_BUF),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,build_id,JSON_TOK_STRING_BUF),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,persistent_crash,JSON_TOK_TRUE),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,drive_profile,JSON_TOK_STRING_BUF),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,auto_instance_id,JSON_TOK_STRING_BUF),
	JSON_OBJ_DESCR_PRIM(struct raw_bootstrap,manual_instance_id,JSON_TOK_STRING_BUF)
};
#undef FIELD
static int copy(char *out,size_t capacity,const char *in)
{
	if (!in || strlen(in)>=capacity) { return -EOVERFLOW; }
	memcpy(out,in,strlen(in)+1);return 0;
}
bool xt_bootstrap_account_matches(const char *username,const char *vm_id,const char *channel)
{
	if (!username || !xt_uuid_valid(vm_id)) { return false; }
	if (channel && !strcmp(channel,"xenova-drive")) { return !strcmp(username,vm_id); }
	if (channel && strcmp(channel,"xenova-telemetry")) { return false; }
	return !strncmp(username,"telemetry-",10) && !strcmp(username+10,vm_id);
}

int xt_bootstrap_decode_channel(char *json,size_t bytes,const char *own_vm_id,
			       const char *channel,struct xt_bootstrap *out)
{
	if(!out)return -EINVAL;
	memset(out,0,sizeof(*out));
	if (!json || !xt_uuid_valid(own_vm_id)) { return -EINVAL; }
	bool drive = channel && !strcmp(channel,"xenova-drive");
	if (channel && !drive && strcmp(channel,"xenova-telemetry")) { return -EINVAL; }
	for(size_t i=0;i+1<bytes;i++)if(json[i]=='\\'){
	 if(json[i+1]=='u')return -EPROTO;
	 ++i;
	}
	struct raw_bootstrap raw={0};
	int64_t found=json_obj_parse(json,bytes,fields,ARRAY_SIZE(fields),&raw);
	if (found<0 || (found&((1LL<<11)-1))!=((1LL<<11)-1)) { return -EPROTO; }
	/* Host injection uses canonical UTF-8, not ASCII \u escapes unsupported by
	 * the pinned STRING_BUF decoder. Refuse rather than publish escaped names. */
	const char *labels[]={raw.hostname,raw.display_name,raw.build_id};
	for(size_t i=0;i<ARRAY_SIZE(labels);i++){
	 if(!xt_utf8_valid(labels[i]))return -EILSEQ;
	 for(const unsigned char *p=(const unsigned char *)labels[i];*p;p++)
	  if(*p<32||*p==127||(*p==0xc2&&p[1]>=0x80&&p[1]<=0x9f))return -EINVAL;
	}
	size_t characters=0;
	for(const unsigned char *p=(const unsigned char *)raw.display_name;*p;p++)
	 if((*p&0xc0)!=0x80)characters++;
	if(characters>63)return -EOVERFLOW;
	if(raw.artifact_sha256[0]){
	 if(strlen(raw.artifact_sha256)!=64)return -EINVAL;
	 for(const char *p=raw.artifact_sha256;*p;p++)if(!((*p>='0'&&*p<='9')||(*p>='a'&&*p<='f')))return -EINVAL;
	}
	if (strcmp(own_vm_id,raw.vm_id) || raw.port<1 || raw.port>65535 || !raw.password[0] ||
	    !xt_bootstrap_account_matches(raw.username,own_vm_id,channel) ||
	    !raw.trusted_internal_plaintext) { return -EACCES; }
	if (!xt_uuid_valid(raw.boot_id) || !xt_uuid_valid(raw.publisher_id)) { return -EINVAL; }
	bool bound=raw.host_boot_id[0] || raw.execution_id[0] || raw.receipt_id[0];
	if (bound && (!xt_uuid_valid(raw.host_boot_id) || !xt_uuid_valid(raw.execution_id) || !xt_uuid_valid(raw.receipt_id))) { return -EINVAL; }
	bool apps=raw.drive_profile[0]||raw.auto_instance_id[0]||raw.manual_instance_id[0];
	if(apps&&(strcmp(raw.drive_profile,"warm-drive-1")||!xt_uuid_valid(raw.auto_instance_id)||
	   !xt_uuid_valid(raw.manual_instance_id)||!strcmp(raw.auto_instance_id,raw.manual_instance_id)))return -EINVAL;
	int rc;
#define COPY(dst,src) do { rc=copy(dst,sizeof(dst),src);if (rc) { memset(out,0,sizeof(*out));return rc; } } while (0)
	COPY(out->identity.vm_id,raw.vm_id);COPY(out->identity.boot_id,raw.boot_id);
	COPY(out->identity.publisher_id,raw.publisher_id);COPY(out->identity.host_boot_id,raw.host_boot_id);
	COPY(out->identity.execution_id,raw.execution_id);COPY(out->identity.receipt_id,raw.receipt_id);
	COPY(out->broker_ipv4,raw.broker_ipv4);COPY(out->username,raw.username);COPY(out->password,raw.password);
	COPY(out->hostname,raw.hostname);COPY(out->display_name,raw.display_name);
	COPY(out->artifact_sha256,raw.artifact_sha256);COPY(out->build_id,raw.build_id);
	out->persistent_crash=raw.persistent_crash;
	COPY(out->drive_profile,raw.drive_profile);COPY(out->auto_instance_id,raw.auto_instance_id);
	COPY(out->manual_instance_id,raw.manual_instance_id);
#undef COPY
	out->port=(uint16_t)raw.port;out->trusted_internal_plaintext=true;return 0;
}

int xt_bootstrap_decode(char *json,size_t bytes,const char *own_vm_id,struct xt_bootstrap *out)
{
	return xt_bootstrap_decode_channel(json,bytes,own_vm_id,"xenova-telemetry",out);
}
