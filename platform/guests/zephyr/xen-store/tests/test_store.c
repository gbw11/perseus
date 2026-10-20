/* SPDX-License-Identifier: Apache-2.0 */
/* Production parser/ring/state code with a deterministic XenStore peer. */
#include <errno.h>
#include <xenova/xenbus_vif.h>
#include <xenova/xenstore_link.h>
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "%s:%d: %s\n", __func__, __LINE__, #x); abort(); } } while (0)
struct kv { char path[256], value[2048]; };
struct peer {
	struct xenstore_domain_interface ring;
	struct xs_client client;
	int64_t clock;
	char request[4113], pending[65536];
	size_t req_have, pending_len, pending_pos, fragment;
	int request_count, writes, commits, aborts, conflicts, watches, unwatches;
	bool no_vif, incomplete, denied, malformed, hold, stop_reading, watch_early, unwatch_late;
	bool empty_directory, multiple;
	bool response_clock, app_acl, data_missing, data_denied, data_read_only, app_signal, flood_clock;
	int clock_reads, jump_at, parent_checks;
	bool oversize, future_id, wrong_type, watch_during_read;
	uint32_t tx;
	struct kv values[32], staged[32];
	size_t value_count, staged_count;
	char watch_path[256], token[16];
	int failure, maps, unmaps, binds, unbinds, masks, unmasks;
	uint64_t store_pfn, store_port, console_port, console_pfn;
};
static struct peer peer;
static const char *front = "/local/domain/42/device/vif/0";
static const char *back = "/local/domain/7/backend/vif/42/0";

static void set_value(struct peer *p, const char *path, const char *value, bool tx)
{
	struct kv *items = tx ? p->staged : p->values;
	size_t *count = tx ? &p->staged_count : &p->value_count;
	size_t i;
	for (i=0; i<*count; i++) { if (!strcmp(items[i].path, path)) { break; } }
	CHECK(i < 32 && strlen(path)<256 && strlen(value)<2048);
	if (i==*count) { (*count)++; }
	strcpy(items[i].path, path); strcpy(items[i].value, value);
}
static const char *get_value(struct peer *p, const char *path, bool tx)
{
	struct kv *items = tx ? p->staged : p->values;
	size_t count = tx ? p->staged_count : p->value_count;
	for (size_t i=0;i<count;i++) { if (!strcmp(items[i].path,path)) { return items[i].value; } }
	return tx ? get_value(p,path,false) : NULL;
}
static void queue(struct peer *p, uint32_t type, uint32_t id, uint32_t tx, const void *data, size_t len)
{
	if (p->pending_pos==p->pending_len) { p->pending_pos=p->pending_len=0; }
	CHECK(p->pending_len+16+len <= sizeof(p->pending));
	struct xsd_sockmsg h={type,id,tx,(uint32_t)len};
	memcpy(p->pending+p->pending_len,&h,16);p->pending_len+=16;
	if (len) { memcpy(p->pending+p->pending_len,data,len);p->pending_len+=len; }
}
static void watch_event(struct peer *p, const char *path, const char *token)
{
	char body[300];size_t n=strlen(path)+1,t=strlen(token)+1;
	memcpy(body,path,n);memcpy(body+n,token,t);
	queue(p,XS_WATCH_EVENT,0,0,body,n+t);
}
static void handle(struct peer *p, struct xsd_sockmsg h, char *payload)
{
	p->request_count++;
	if (p->oversize) {
		struct xsd_sockmsg bad={h.type,h.req_id,h.tx_id,4097};
		memcpy(p->pending,&bad,16);p->pending_len=16;p->pending_pos=0;return;
	}
	if (p->future_id) { queue(p,h.type,h.req_id+1,h.tx_id,"bad",3);return; }
	if (p->wrong_type) { queue(p,XS_CONTROL,h.req_id,h.tx_id,"bad",3);return; }
	const char *error=NULL,*value=NULL;
	char result[32];
	switch (h.type) {
	case XS_READ:
		if (p->denied) { error="EACCES";break; }
		if (p->incomplete && strstr(payload,"/backend")) { error="ENOENT";break; }
		if (!strcmp(payload,"domid")) { value="42"; }
		else { value=get_value(p,payload,h.tx_id!=0); }
		if (p->watch_during_read) { watch_event(p,p->watch_path,p->token);p->watch_during_read=false; }
		if (!value) { error="ENOENT"; }
		break;
	case XS_DIRECTORY:
		if (p->empty_directory) { queue(p,h.type,h.req_id,h.tx_id,NULL,0);return; }
		if (p->multiple) { queue(p,h.type,h.req_id,h.tx_id,"0\0001",4);return; }
		if (p->no_vif) { error="ENOENT";break; }
		if (p->app_acl && strstr(payload,"/data/xenova-g1-")) {
			if (!get_value(p,payload,h.tx_id!=0)) { error="ENOENT";break; }
			const char *entry=NULL;
			for (size_t i=0;i<p->value_count;i++) {
				size_t length=strlen(payload);
				if (!strncmp(p->values[i].path,payload,length) && p->values[i].path[length]=='/') {
					entry=p->values[i].path+length+1;break;
				}
			}
			queue(p,h.type,h.req_id,h.tx_id,entry,entry ? strlen(entry)+1 : 0);return;
		}
		if (strcmp(payload,"/local/domain/42/device/vif")) { error="ENOENT";break; }
		value="0";break;
	case XS_GET_PERMS:
		if (p->app_acl && !strcmp(payload,"/local/domain/42")) {
			queue(p,h.type,h.req_id,h.tx_id,"n0\0r42",7);return;
		}
		if (p->app_acl && !strcmp(payload,"/local/domain/42/data")) {
			p->parent_checks++;
			if (p->data_read_only) { queue(p,h.type,h.req_id,h.tx_id,"n0\0r42",7);return; }
			if (p->data_missing) { error="ENOENT";break; }
			if (p->data_denied) { error="EACCES";break; }
		}
		value="n42";break;
	case XS_WRITE: {
		p->writes++;
		if (p->app_acl) { CHECK(!strncmp(payload,"/local/domain/42/data/xenova-g1-",32)); }
		const char *data=payload+strlen(payload)+1;
		if (h.tx_id) { CHECK(h.tx_id==p->tx); }
		set_value(p,payload,data,h.tx_id!=0);
		value="OK";break;
	}
	case XS_MKDIR: case XS_RM: value="OK";break;
	case XS_TRANSACTION_START:
		p->tx++;p->staged_count=0;
		snprintf(result,sizeof(result),"%u",p->tx);value=result;break;
	case XS_TRANSACTION_END:
		CHECK(h.tx_id==p->tx);
		if (payload[0]=='F') { p->aborts++;p->staged_count=0; }
		else {
			p->commits++;
			if (p->conflicts) { p->conflicts--;error="EAGAIN";p->staged_count=0;break; }
			for (size_t i=0;i<p->staged_count;i++) { set_value(p,p->staged[i].path,p->staged[i].value,false); }
			p->staged_count=0;
		}
		value="OK";break;
	case XS_WATCH:
		p->watches++;strcpy(p->watch_path,payload);strcpy(p->token,payload+strlen(payload)+1);
		if (p->watch_early) {
			set_value(p,payload,"changed-before-ack",false);
			watch_event(p,payload,p->token);
		}
		value="OK";break;
	case XS_UNWATCH:
		p->unwatches++;
		if (p->unwatch_late) { watch_event(p,payload,payload+strlen(payload)+1); }
		value="OK";break;
	default: error="ENOSYS";break;
	}
	if (error) { queue(p,XS_ERROR,h.req_id,h.tx_id,error,strlen(error)+1); }
	else { queue(p,h.type,h.req_id,h.tx_id,value,strlen(value)+1); }
}
static void step(struct peer *p)
{
	if (!p->stop_reading) {
		size_t n=0;
		while (p->ring.req_cons!=p->ring.req_prod && n<p->fragment) {
			p->request[p->req_have++]=p->ring.req[MASK_XENSTORE_IDX(p->ring.req_cons++)];
			n++;
			if (p->req_have>=16) {
				struct xsd_sockmsg h;memcpy(&h,p->request,16);
				CHECK(h.len<=4096);
				if (p->req_have==16+h.len) {
					p->request[p->req_have]=0;
					handle(p,h,p->request+16);p->req_have=0;
				}
			}
		}
	}
	if (p->hold) { return; }
	for (size_t n=0;p->pending_pos<p->pending_len && n<p->fragment &&
	     p->ring.rsp_prod-p->ring.rsp_cons<1024;n++) {
		p->ring.rsp[MASK_XENSTORE_IDX(p->ring.rsp_prod++)]=p->pending[p->pending_pos++];
	}
}
static int64_t now(void *ctx)
{
	struct peer *p=ctx;
	if ((!p->response_clock || p->request_count) && ++p->clock_reads==p->jump_at) { p->clock+=5; }
	return p->clock;
}
static int notify(void *ctx)
{
	struct peer *p=ctx;
	if (p->flood_clock) { p->clock++; }
	step(p);return 0;
}
static int wait_event(void *ctx,int64_t end)
{
	struct peer *p=ctx;
	if (p->clock>=end) { return -ETIMEDOUT; }
	p->clock++;
	if (p->app_signal && strstr(p->watch_path,"/data/")) {
		set_value(p,p->watch_path,"dom0-marker",false);
		watch_event(p,p->watch_path,p->token);p->app_signal=false;
	}
	step(p);return 0;
}
static struct xs_io io(struct peer *p)
{
	return (struct xs_io){now,wait_event,notify,p};
}
static void reset(void)
{
	memset(&peer,0,sizeof(peer));peer.fragment=4096;
	peer.store_pfn=0x12345;peer.store_port=17;peer.console_port=18;peer.console_pfn=0x12346;
	struct xs_io transport=io(&peer);
	CHECK(xs_init(&peer.client,&peer.ring,&transport)==0);
	char path[256];
	snprintf(path,sizeof(path),"%s/backend",front);set_value(&peer,path,back,false);
	snprintf(path,sizeof(path),"%s/backend-id",front);set_value(&peer,path,"7",false);
	snprintf(path,sizeof(path),"%s/mac",front);set_value(&peer,path,"02:16:3e:ab:cd:ef",false);
	snprintf(path,sizeof(path),"%s/state",front);set_value(&peer,path,"1",false);
	snprintf(path,sizeof(path),"%s/state",back);set_value(&peer,path,"2",false);
}
static void basic(void)
{
	char text[64],entries[64];size_t len;
	CHECK(xs_write(&peer.client,0,"/local/domain/42/data/test","hello",5,100)==0);
	CHECK(xs_read_text(&peer.client,0,"/local/domain/42/data/test",text,sizeof(text),100)==0);
	CHECK(!strcmp(text,"hello"));
	CHECK(xs_directory(&peer.client,0,"/local/domain/42/device/vif",entries,sizeof(entries),&len,100)==0);
	CHECK(len==2 && !strcmp(entries,"0"));
	CHECK(xs_get_perms(&peer.client,"/local/domain/42",entries,sizeof(entries),&len,100)==0);
	CHECK(!strcmp(entries,"n42"));
}
static void wrap_and_fragment(void)
{
	peer.fragment=3;
	peer.ring.req_cons=peer.ring.req_prod=UINT32_MAX-7;
	peer.ring.rsp_cons=peer.ring.rsp_prod=UINT32_MAX-5;
	basic();
}
static int transaction_body(struct xs_client *c,uint32_t tx,int64_t end,void *ctx)
{
	const char *value = ctx ? "aborted" : "commit";
	int rc=xs_write(c,tx,"/local/domain/42/data/tx",value,strlen(value),xs_remaining(c,end));
	return rc ? rc : (ctx ? -EINVAL : 0);
}
static void transactions(void)
{
	unsigned int attempts;
	CHECK(xs_transaction(&peer.client,3,100,transaction_body,NULL,&attempts)==0 && attempts==1);
	CHECK(!strcmp(get_value(&peer,"/local/domain/42/data/tx",false),"commit"));
	CHECK(xs_transaction(&peer.client,3,100,transaction_body,&peer,&attempts)==-EINVAL);
	CHECK(peer.aborts==1 && peer.client.active_tx==0);
	CHECK(!strcmp(get_value(&peer,"/local/domain/42/data/tx",false),"commit"));
}
static void conflict_limit(void)
{
	unsigned int attempts;peer.conflicts=3;
	CHECK(xs_transaction(&peer.client,3,100,transaction_body,NULL,&attempts)==-EAGAIN);
	CHECK(attempts==3 && peer.commits==3 && peer.client.active_tx==0);
}
static void conflict_then_commit(void)
{
	unsigned int attempts;peer.conflicts=1;
	CHECK(xs_transaction(&peer.client,3,100,transaction_body,NULL,&attempts)==0 && attempts==2);
}
static void error_response(void)
{
	char value[32];peer.denied=true;
	CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),100)==-EACCES);
	CHECK(!strcmp(peer.client.last_error,"EACCES") && !peer.client.poisoned);
}
static void input_bounds(void)
{
	char long_path[257];memset(long_path,'a',256);long_path[256]=0;
	char text[8],value[2049]={0};
	CHECK(xs_read_text(&peer.client,0,long_path,text,sizeof(text),100)==-EINVAL);
	CHECK(xs_write(&peer.client,0,"/a",value,sizeof(value),100)==-EINVAL);
	CHECK(xs_read_text(&peer.client,0,"/../other",text,sizeof(text),100)==-EINVAL);
	CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),-1)==-EINVAL);
	CHECK(peer.request_count==0);
}
static void small_response_buffer(void)
{
	char text[2];CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),100)==-ENOBUFS);
	char good[16];CHECK(xs_read_text(&peer.client,0,"domid",good,sizeof(good),100)==0);
}
static void oversize_response(void)
{
	peer.oversize=true;char text[16];
	CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),100)==-EMSGSIZE);
	CHECK(peer.client.poisoned);
}
static void corrupt_index(void)
{
	peer.ring.rsp_prod=1025;char text[16];
	CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),100)==-EPROTO);
}
static void unexpected_response_id(void)
{
	peer.future_id=true;char text[16];
	CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),100)==-EPROTO);
}
static void mismatched_type(void)
{
	peer.wrong_type=true;char text[16];
	CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),100)==-EPROTO);
}
static void delayed_response(void)
{
	peer.hold=true;char text[32];
	CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),5)==-ETIMEDOUT);
	CHECK(!peer.client.poisoned);
	set_value(&peer,"/value","new-response",false);peer.hold=false;
	CHECK(xs_read_text(&peer.client,0,"/value",text,sizeof(text),100)==0);
	CHECK(!strcmp(text,"new-response") && peer.client.late_responses==1);
}
static void partial_timeout(void)
{
	peer.fragment=1;char text[32];
	CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),25)==-ETIMEDOUT);
	peer.fragment=4096;
	CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),100)==0);
	CHECK(!strcmp(text,"42") && peer.client.late_responses==1);
}
static void partial_send_timeout(void)
{
	peer.stop_reading=true;char value[2048];memset(value,'x',sizeof(value));
	CHECK(xs_write(&peer.client,0,"/value",value,sizeof(value),5)==-ETIMEDOUT);
	CHECK(peer.client.poisoned);
}
static void watch_before_ack(void)
{
	uint32_t watch;peer.watch_early=true;struct xs_watch_event event;
	CHECK(xs_watch(&peer.client,"/local/domain/42/data/watch",&watch,100)==0);
	CHECK(xs_watch_next(&peer.client,watch,&event,100)==0);
	CHECK(event.initial && event.notifications==1);
	char text[64];
	CHECK(xs_read_text(&peer.client,0,"/local/domain/42/data/watch",text,sizeof(text),100)==0);
	CHECK(!strcmp(text,"changed-before-ack"));
}
static void watch_during_request(void)
{
	uint32_t id;struct xs_watch_event event;char text[32];
	CHECK(xs_watch(&peer.client,"/local/domain/42/data/watch",&id,100)==0);
	CHECK(xs_watch_next(&peer.client,id,&event,100)==0 && event.initial);
	peer.watch_during_read=true;
	CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),100)==0 && !strcmp(text,"42"));
	CHECK(xs_watch_next(&peer.client,id,&event,100)==0 && event.notifications==1 && !event.initial);
}
static void unwatch_late(void)
{
	uint32_t id,second;struct xs_watch_event event;char token[16];
	CHECK(xs_watch(&peer.client,"/watch",&id,100)==0);strcpy(token,peer.token);
	peer.unwatch_late=true;
	CHECK(xs_unwatch(&peer.client,id,100)==0);
	watch_event(&peer,"/watch",token);step(&peer);
	CHECK(xs_watch(&peer.client,"/watch",&second,100)==0 && second!=id);
	CHECK(strcmp(token,peer.token));
	CHECK(xs_watch_next(&peer.client,second,&event,100)==0 && event.initial && event.notifications==0);
	CHECK(xs_watch_next(&peer.client,second,&event,5)==-ETIMEDOUT);
}
static void watch_limit(void)
{
	uint32_t id;
	for (int i=0;i<4;i++) { CHECK(xs_watch(&peer.client,"/watch",&id,100)==0); }
	CHECK(xs_watch(&peer.client,"/watch",&id,100)==-ENOSPC);
	CHECK(peer.watches==4);
}
static void watch_registration_timeout_cleanup(void)
{
	uint32_t id;peer.hold=true;
	CHECK(xs_watch(&peer.client,"/watch",&id,5)==-ETIMEDOUT);
	peer.hold=false;
	CHECK(xs_close(&peer.client,100)==0);
	CHECK(peer.unwatches==1);
}
static void late_transaction_start_cleanup(void)
{
	uint32_t tx;peer.hold=true;
	CHECK(xs_transaction_start(&peer.client,&tx,5)==-ETIMEDOUT);
	peer.hold=false;
	CHECK(xs_close(&peer.client,100)==0);
	CHECK(peer.aborts==1);
}
static void lost_transaction_start_cleanup(void)
{
	uint32_t tx;peer.hold=true;
	CHECK(xs_transaction_start(&peer.client,&tx,5)==-ETIMEDOUT);
	CHECK(xs_close(&peer.client,5)==-ETIMEDOUT);
}
static void no_vif(void)
{
	struct xvif v;peer.no_vif=true;
	CHECK(xvif_discover(&peer.client,&v,100)==-ENODEV && peer.writes==0);
}
static void discover_vif(void)
{
	struct xvif v;
	CHECK(xvif_discover(&peer.client,&v,100)==0);
	CHECK(v.domid==42 && v.device==0 && v.backend_domid==7);
	CHECK(!strcmp(v.backend,back) && v.frontend_state==1 && v.backend_state==2);
	CHECK(v.mac[0]==2 && v.mac[5]==0xef && peer.writes==0);
}
static void malformed_vif(void)
{
	struct xvif v;char path[256];snprintf(path,sizeof(path),"%s/backend",front);
	set_value(&peer,path,"/local/domain/0/backend/vif/99/0",false);
	CHECK(xvif_discover(&peer.client,&v,100)==-EPROTO);
}
static void bad_mac(void)
{
	struct xvif v;char path[256];snprintf(path,sizeof(path),"%s/mac",front);
	set_value(&peer,path,"ff:ff:ff:ff:ff:ff",false);
	CHECK(xvif_discover(&peer.client,&v,100)==-EPROTO);
}
static void backend_not_ready(void)
{
	struct xvif v;peer.incomplete=true;
	CHECK(xvif_probe(&peer.client,&v,100)==-EAGAIN);
	CHECK(xvif_discover(&peer.client,&v,25)==-ETIMEDOUT && peer.clock==25);
}
static void vif_denied(void)
{
	struct xvif v;peer.denied=true;
	CHECK(xvif_discover(&peer.client,&v,100)==-EACCES);
}
static void backend_state_timeout(void)
{
	struct xvif v;uint32_t id;
	CHECK(xvif_discover(&peer.client,&v,100)==0);
	CHECK(xvif_watch_backend(&peer.client,&v,&id,100)==0);
	CHECK(xvif_wait_backend(&peer.client,&v,id,XenbusStateConnected,30)==-ETIMEDOUT);
	CHECK(v.backend_state==XenbusStateInitWait && v.frontend_state==XenbusStateInitialising && peer.writes==0);
}
static void changed_backend(void)
{
	struct xvif v;char path[256];
	CHECK(xvif_discover(&peer.client,&v,100)==0);
	snprintf(path,sizeof(path),"%s/backend",front);set_value(&peer,path,"/changed",false);
	CHECK(xvif_refresh(&peer.client,&v,100)==-EXDEV);
}
static void concurrent_request_rejected(void)
{
	peer.client.busy=true;char value[16];
	CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),10)==-EBUSY && peer.request_count==0);
	peer.client.busy=false;
}
static int parameter(void *ctx,enum xs_parameter which,uint64_t *value)
{
	struct peer *p=ctx;
	if (p->failure==1+(int)which) { return -EIO; }
	uint64_t values[]={p->store_pfn,p->store_port,p->console_port,p->console_pfn};
	*value=values[which];return 0;
}
static int map(void *ctx,uintptr_t physical,void **out)
{
	struct peer *p=ctx;p->maps++;CHECK(physical==p->store_pfn*4096);
	if (p->failure==5) { return -ENOMEM; }
	*out=&p->ring;return 0;
}
static void unmap(void *ctx,void *addr)
{
	struct peer *p=ctx;p->unmaps++;CHECK(addr==&p->ring);
}
static int bind(void *ctx,uint32_t port)
{
	struct peer *p=ctx;p->binds++;CHECK(port==p->store_port && port!=p->console_port);
	return p->failure==6 ? -EIO : 0;
}
static void unbind(void *ctx,uint32_t port)
{
	struct peer *p=ctx;p->unbinds++;CHECK(port==p->store_port && port!=p->console_port);
}
static void mask(void *ctx,uint32_t port)
{
	struct peer *p=ctx;p->masks++;CHECK(port==p->store_port && port!=p->console_port);
}
static int unmask(void *ctx,uint32_t port)
{
	struct peer *p=ctx;p->unmasks++;CHECK(port==p->store_port && port!=p->console_port);
	return p->failure==7 ? -EIO : 0;
}
static struct xs_link_ops link_ops={parameter,map,unmap,bind,unbind,mask,unmask};
static void init_failure_cleanup(void)
{
	for (int i=1;i<=7;i++) {
		reset();peer.failure=i;struct xs_link link;struct xs_io transport=io(&peer);
		CHECK(xs_link_open(&link,&link_ops,&transport)<0);
		CHECK(!link.open && !link.mapping);
		CHECK(peer.unmaps==(i>5 ? 1 : 0));
		CHECK(peer.unbinds==(i==7 ? 1 : 0));
	}
}
static void console_isolation(void)
{
	struct xs_link link;struct xs_io transport=io(&peer);
	peer.store_port=peer.console_port;
	CHECK(xs_link_open(&link,&link_ops,&transport)==-EINVAL && !peer.binds && !peer.maps);
	peer.store_port=17;peer.store_pfn=peer.console_pfn;
	CHECK(xs_link_open(&link,&link_ops,&transport)==-EINVAL && !peer.binds && !peer.maps);
}
static void successful_link_cleanup(void)
{
	struct xs_link link;struct xs_io transport=io(&peer);
	CHECK(xs_link_open(&link,&link_ops,&transport)==0);
	uint32_t id;CHECK(xs_watch(&link.client,"/watch",&id,100)==0);
	CHECK(xs_link_close(&link,100)==0);
	CHECK(peer.unwatches==1 && peer.unmaps==1 && peer.unbinds==1 && peer.masks==1);
	CHECK(xs_link_close(&link,100)==0 && peer.unmaps==1);
}
static void busy_watch_does_not_mutate(void)
{
	uint32_t id=99;
	peer.client.busy=true;
	CHECK(xs_watch(&peer.client,"/watch",&id,10)==-EBUSY);
	CHECK(peer.client.next_watch==0 && id==99 && peer.client.watches[0].state==XS_W_FREE);
	peer.client.busy=false;
	CHECK(xs_watch(&peer.client,"/watch",&id,100)==0);
	peer.client.busy=true;
	CHECK(xs_unwatch(&peer.client,id,10)==-EBUSY);
	CHECK(peer.client.watches[0].state==XS_W_ACTIVE);
	peer.client.busy=false;
}
static void zero_empty_directory(void)
{
	char text[32];size_t len;
	peer.empty_directory=true;
	CHECK(xs_directory(&peer.client,0,"/empty",text,sizeof(text),&len,100)==0 && len==0);
}
static void malformed_watch_event(void)
{
	uint32_t id;char text[16];
	CHECK(xs_watch(&peer.client,"/watch",&id,100)==0);
	queue(&peer,XS_WATCH_EVENT,0,0,"not-a-token",11);step(&peer);
	CHECK(xs_read_text(&peer.client,0,"domid",text,sizeof(text),100)==-EPROTO);
}
static void bad_state(void)
{
	struct xvif v;char path[256];snprintf(path,sizeof(path),"%s/state",back);
	set_value(&peer,path,"9",false);
	CHECK(xvif_discover(&peer.client,&v,100)==-EPROTO);
}
static void multiple_vifs(void)
{
	peer.multiple=true;
	struct xvif v;
	CHECK(xvif_discover(&peer.client,&v,100)==-E2BIG);
}
static void link_close_while_busy(void)
{
	struct xs_link link;struct xs_io transport=io(&peer);
	CHECK(xs_link_open(&link,&link_ops,&transport)==0);
	link.client.busy=true;
	CHECK(xs_link_close(&link,100)==-EBUSY && !peer.unmaps && !peer.unbinds);
	link.client.busy=false;
	CHECK(xs_link_close(&link,100)==0);
}
static void request_id_exhaustion(void)
{
	char value[16];peer.client.next_request=UINT32_MAX;
	CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),100)==-EOVERFLOW);
	CHECK(peer.request_count==0);
}
static void connection_error(void)
{
	peer.ring.server_features=XENSTORE_SERVER_FEATURE_ERROR;peer.ring.error=XENSTORE_ERROR_COMM;
	char value[16];CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),10)==-EIO);
}
static void large_ring_roundtrip(void)
{
	char value[1801], reply[1802];
	memset(value,'x',1800);value[1800]=0;
	CHECK(xs_write(&peer.client,0,"/large",value,1800,100)==0);
	CHECK(xs_read_text(&peer.client,0,"/large",reply,sizeof(reply),100)==0);
	CHECK(!strcmp(value,reply));
}
static void pending_limit_and_close(void)
{
	char value[16];peer.hold=true;
	for (int i=0;i<XS_PENDING_LIMIT;i++) {
		CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),2)==-ETIMEDOUT);
	}
	CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),2)==-EBUSY);
	CHECK(peer.request_count==XS_PENDING_LIMIT);
	peer.hold=false;
	CHECK(xs_close(&peer.client,100)==0);
	CHECK(peer.client.late_responses==XS_PENDING_LIMIT);
}
static void backend_watch_reread(void)
{
	struct xvif v;uint32_t id;struct xs_watch_event event;
	CHECK(xvif_discover(&peer.client,&v,100)==0);
	CHECK(xvif_watch_backend(&peer.client,&v,&id,100)==0);
	CHECK(xs_watch_next(&peer.client,id,&event,100)==0 && event.initial);
	char path[256];snprintf(path,sizeof(path),"%s/state",back);
	set_value(&peer,path,"5",false);watch_event(&peer,path,peer.token);step(&peer);
	CHECK(xs_watch_next(&peer.client,id,&event,100)==0 && event.notifications==1);
	CHECK(xvif_refresh(&peer.client,&v,100)==0);
	CHECK(v.frontend_state==XenbusStateInitialising && v.backend_state==XenbusStateClosing);
	CHECK(peer.writes==0);
}
static void root_watch_and_relative_rejection(void)
{
	uint32_t id;struct xs_watch_event event;
	CHECK(xs_watch(&peer.client,"relative",&id,100)==-EINVAL);
	CHECK(xs_watch(&peer.client,"/",&id,100)==0);
	CHECK(xs_watch_next(&peer.client,id,&event,100)==0 && event.initial);
	watch_event(&peer,"/local/domain/42",peer.token);step(&peer);
	CHECK(xs_watch_next(&peer.client,id,&event,100)==0 && event.notifications==1);
}
static void early_response_rejected(void)
{
	char value[16];peer.stop_reading=true;
	queue(&peer,XS_READ,1,0,"bad",4);
	CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),100)==-EPROTO);
	CHECK(peer.client.poisoned);
}
static void pending_window_recovers(void)
{
	char value[16];peer.hold=true;
	for (int i=0;i<XS_PENDING_LIMIT;i++) {
		CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),2)==-ETIMEDOUT);
	}
	peer.hold=false;step(&peer);
	CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),100)==0);
	CHECK(!strcmp(value,"42") && peer.client.late_responses==XS_PENDING_LIMIT);
}
/* Execute the production app, replacing only kernel/transport boundaries. */
#define main g1_app_main
#define transaction_body app_transaction_body
#include "../../xenstore-test-app/src/main.c"
#undef main
#undef transaction_body
int xs_zephyr_open(struct xs_zephyr *z)
{
	memset(z,0,sizeof(*z));memset(&vif,0,sizeof(vif));
	struct xs_io transport_io=io(&peer);z->link.port=17;
	return xs_init(&z->link.client,&peer.ring,&transport_io);
}
int xs_zephyr_close(struct xs_zephyr *z,int ms) { return xs_close(&z->link.client,ms); }
uint64_t k_cycle_get_64(void) { return 0xab; }
void k_sleep(int ms) { (void)ms; }
static void app_root_ro_data_rw(void)
{
	peer.app_acl=true;peer.app_signal=true;
	CHECK(g1_app_main()==0 && peer.writes>0 && peer.commits>=2);
}
static void app_data_denied(void)
{
	peer.app_acl=true;peer.data_denied=true;
	CHECK(g1_app_main()==-EACCES && peer.parent_checks==1 && peer.writes==0 && peer.commits==0);
}
static void app_data_missing(void)
{
	peer.app_acl=true;peer.data_missing=true;
	CHECK(g1_app_main()==-ENOENT && peer.parent_checks==1 && peer.writes==0 && peer.commits==0);
}
static void app_data_read_only(void)
{
	peer.app_acl=true;peer.data_read_only=true;
	CHECK(g1_app_main()==-EACCES && peer.parent_checks==1 && peer.writes==0 && peer.commits==0);
}
static void app_existing_node_not_claimed(void)
{
	peer.app_acl=true;
	set_value(&peer,"/local/domain/42/data/xenova-g1-ab","existing",false);
	CHECK(g1_app_main()==-EEXIST && peer.writes==0 && peer.aborts==1);
	CHECK(!strcmp(get_value(&peer,"/local/domain/42/data/xenova-g1-ab",false),"existing"));
}
static void response_deadline(int jump)
{
	char value[16];peer.hold=true;peer.response_clock=true;peer.jump_at=jump ? jump-1 : 0;
	CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),5)==-ETIMEDOUT);
	CHECK(!peer.client.poisoned);
	peer.hold=false;peer.jump_at=0;
	CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),100)==0);
	CHECK(!strcmp(value,"42") && peer.client.late_responses==1);
}
static void response_deadline_before(void) { response_deadline(2); }
static void response_deadline_between(void) { response_deadline(3); }
static void response_deadline_wait(void) { response_deadline(0); }
static void watch_deadline(int jump)
{
	uint32_t id;struct xs_watch_event event;char value[16];
	CHECK(xs_watch(&peer.client,"/watch",&id,100)==0);
	CHECK(xs_watch_next(&peer.client,id,&event,100)==0);
	peer.clock_reads=0;peer.jump_at=jump;
	CHECK(xs_watch_next(&peer.client,id,&event,5)==-ETIMEDOUT);
	CHECK(!peer.client.poisoned);
	peer.jump_at=0;watch_event(&peer,"/watch",peer.token);step(&peer);
	CHECK(xs_watch_next(&peer.client,id,&event,100)==0 && event.notifications==1);
	CHECK(xs_read_text(&peer.client,0,"domid",value,sizeof(value),100)==0);
}
static void watch_deadline_before(void) { watch_deadline(2); }
static void watch_deadline_between(void) { watch_deadline(3); }
static void watch_deadline_wait(void) { watch_deadline(0); }
static void stalled_send_watch_flood(bool partial)
{
	uint32_t id;struct xs_watch_event event;char value[2048];memset(value,'x',sizeof(value));
	CHECK(xs_watch(&peer.client,"/watch",&id,100)==0);
	CHECK(xs_watch_next(&peer.client,id,&event,100)==0);
	peer.stop_reading=true;
	if (!partial) { peer.ring.req_prod=peer.ring.req_cons+1024; }
	for (int i=0;i<50;i++) { watch_event(&peer,"/watch",peer.token); }
	step(&peer);peer.flood_clock=true;int64_t start=peer.clock;
	CHECK(xs_write(&peer.client,0,"/value",value,sizeof(value),5)==-ETIMEDOUT);
	CHECK(peer.clock-start<=8);
	CHECK(peer.client.poisoned==partial);
	CHECK(peer.client.watches[0].notifications>0);
	if (!partial) {
		for (int i=0;i<XS_PENDING_LIMIT;i++) { CHECK(!peer.client.pending[i]); }
		peer.flood_clock=false;peer.stop_reading=false;
		peer.ring.req_cons=peer.ring.req_prod=0; /* discard the peer-owned blocking fixture */
		char reply[16];
		CHECK(xs_read_text(&peer.client,0,"domid",reply,sizeof(reply),100)==0);
	}
}
static void stalled_send_watch_flood_zero(void) { stalled_send_watch_flood(false); }
static void stalled_send_watch_flood_partial(void) { stalled_send_watch_flood(true); }

int main(int argc, char **argv)
{
	setbuf(stdout, NULL);
	struct { const char *name; void (*run)(void); } cases[]={
		{"app_data_read_only", app_data_read_only},
		{"app_existing_node_not_claimed", app_existing_node_not_claimed},
		{"app_root_ro_data_rw", app_root_ro_data_rw},
{"app_data_denied", app_data_denied},
{"app_data_missing", app_data_missing},
{"response_deadline_before", response_deadline_before},
{"response_deadline_between", response_deadline_between},
{"response_deadline_wait", response_deadline_wait},
{"watch_deadline_before", watch_deadline_before},
{"watch_deadline_between", watch_deadline_between},
{"watch_deadline_wait", watch_deadline_wait},
{"stalled_send_watch_flood_zero", stalled_send_watch_flood_zero},
{"stalled_send_watch_flood_partial", stalled_send_watch_flood_partial},
		{"basic", basic},
		{"wrap_and_fragment", wrap_and_fragment},
		{"transactions", transactions},
		{"conflict_limit", conflict_limit},
		{"conflict_then_commit", conflict_then_commit},
		{"error_response", error_response},
		{"input_bounds", input_bounds},
		{"small_response_buffer", small_response_buffer},
		{"oversize_response", oversize_response},
		{"corrupt_index", corrupt_index},
		{"unexpected_response_id", unexpected_response_id},
		{"mismatched_type", mismatched_type},
		{"delayed_response", delayed_response},
		{"partial_timeout", partial_timeout},
		{"partial_send_timeout", partial_send_timeout},
		{"watch_before_ack", watch_before_ack},
		{"watch_during_request", watch_during_request},
		{"unwatch_late", unwatch_late},
		{"watch_limit", watch_limit},
		{"watch_registration_timeout_cleanup", watch_registration_timeout_cleanup},
		{"late_transaction_start_cleanup", late_transaction_start_cleanup},
		{"lost_transaction_start_cleanup", lost_transaction_start_cleanup},
		{"no_vif", no_vif},
		{"discover_vif", discover_vif},
		{"malformed_vif", malformed_vif},
		{"bad_mac", bad_mac},
		{"backend_not_ready", backend_not_ready},
		{"vif_denied", vif_denied},
		{"backend_state_timeout", backend_state_timeout},
		{"changed_backend", changed_backend},
		{"concurrent_request_rejected", concurrent_request_rejected},
		{"init_failure_cleanup", init_failure_cleanup},
		{"console_isolation", console_isolation},
		{"successful_link_cleanup", successful_link_cleanup},
		{"connection_error", connection_error},
		{"busy_watch_does_not_mutate", busy_watch_does_not_mutate},
		{"zero_empty_directory", zero_empty_directory},
		{"malformed_watch_event", malformed_watch_event},
		{"bad_state", bad_state},
		{"multiple_vifs", multiple_vifs},
		{"link_close_while_busy", link_close_while_busy},
		{"request_id_exhaustion", request_id_exhaustion},
		{"large_ring_roundtrip", large_ring_roundtrip},
		{"pending_limit_and_close", pending_limit_and_close},
		{"backend_watch_reread", backend_watch_reread},
		{"root_watch_and_relative_rejection", root_watch_and_relative_rejection},
		{"early_response_rejected", early_response_rejected},
		{"pending_window_recovers", pending_window_recovers}
	};
	for (size_t i=0;i<sizeof(cases)/sizeof(cases[0]);i++) {
		if (argc>1 && strcmp(argv[1],cases[i].name)) { continue; }
		reset();cases[i].run();printf("PASS %s\n",cases[i].name);
	}
	printf("PASS %zu production-code cases\n",sizeof(cases)/sizeof(cases[0]));
	return 0;
}
