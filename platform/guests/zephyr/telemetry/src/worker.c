/* SPDX-License-Identifier: Apache-2.0 */
/* Bounded single-owner MQTT lifecycle. No callbacks run from measured loop hooks. */
#include <xenova/telemetry.h>
#include <xenova/diagnostics.h>
#include <zephyr/net/mqtt.h>
#include <zephyr/net/net_if.h>
#include <zephyr/net/hostname.h>
#include <zephyr/net/socket.h>
#include <zephyr/sys/atomic.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "socket_transport.h"

#define IO_MS 250
#define ACK_MS 2000
#define SNAPSHOT_MS 30000
#define BURST_BYTES 8192
#define BYTES_PER_SECOND 16384
K_THREAD_STACK_DEFINE(xt_worker_stack,CONFIG_XENOVA_TELEMETRY_STACK_SIZE);
static struct k_thread worker_thread;
K_MUTEX_DEFINE(api_lock);
K_MUTEX_DEFINE(status_lock);
static atomic_t stopping, snapshot_requested;
static struct k_spinlock admission_lock;
static bool admit(void)
{
	k_spinlock_key_t key=k_spin_lock(&admission_lock);
	bool allowed=!atomic_get(&stopping);
	k_spin_unlock(&admission_lock,key);return allowed;
}
static bool launched;
static xt_bootstrap_loader load_bootstrap;
static void *loader_context;
static struct xt_inventory inventory;
static struct xt_status status_view;
static struct xt_publisher publisher;
static struct xt_bootstrap bootstrap;
static struct mqtt_client client;
static struct mqtt_utf8 username,password;
static struct net_sockaddr_in broker;
static struct xt_socket socket_io={.fd=-1};
static uint8_t tx[XT_PAYLOAD_MAX+256],rx[512];
static struct xt_message outgoing,inflight;
static struct xt_observation current,frozen;
static struct xt_observation cpu_frozen;
static char cpu_sample[37];
static unsigned cpu_part,cpu_parts;
static int64_t cpu_due,cpu_expiry;
static bool cpu_active;
static char current_sample[37],frozen_sample[37],frozen_id[37];
static char guest_boot_id[37],previous_publisher_id[37],client_id[40],interface_name[32];
static bool connected,connecting,acked,protocol_error,pending,inventory_pending;
static uint16_t packet_id,pending_packet_id;
static enum xt_kind pending_kind;
static uint64_t pending_event_seq;
static int64_t ack_deadline,retry_time,connect_deadline;
static unsigned backoff_ms;
static bool snapshot_active;
static enum xt_kind snapshot_kind;
static unsigned snapshot_part,snapshot_parts;
static int64_t snapshot_deadline;
static unsigned tokens;
static int64_t token_time;
static int connection_error;

static int collect_current(void)
{
	(void)xt_observe(&current,false);
	bool failed=current.collection_rc ||
		(current.heap_rc && current.heap_rc!=-ENOTSUP) ||
		(current.network_rc && current.network_rc!=-ENOTSUP && current.network_rc!=-ENOENT);
	if(current.resources.cpu.rc && current.resources.cpu.rc!=-ENOTSUP &&
	   current.resources.cpu.rc!=-ENODATA)failed=true;
	for(size_t i=0;i<current.resources.regions;i++)if(current.resources.memory[i].rc)failed=true;
	xt_collection_result(&publisher.counters,failed,current.end_ms);
	if (!current.history_rc) { xt_capture_events(&publisher,&current.history); }
	publisher.counters.collect_us+=current.elapsed_us;
	return xt_sample_id(&publisher,current_sample);
}

static void publish_status(int rc)
{
	if (!k_mutex_lock(&status_lock,K_MSEC(20))) {
		status_view=(struct xt_status){.identity=publisher.identity,.running=true,.identified=publisher.identity.vm_id[0]!=0,
			.connected=connected,.last_rc=rc,.observed_uptime_ms=k_uptime_get(),.counters=publisher.counters};
		k_mutex_unlock(&status_lock);
	}
}

static void on_event(struct mqtt_client *m,const struct mqtt_evt *event)
{
	ARG_UNUSED(m);
	if (event->type==MQTT_EVT_CONNACK) {
		connecting=false;connected=event->result==0;
		if (connected) { inventory_pending=true;backoff_ms=1000; }
		else { protocol_error=true; }
	} else if (event->type==MQTT_EVT_DISCONNECT) { connected=false;connecting=false;protocol_error=true; }
	else if (event->type==MQTT_EVT_PUBACK && pending && event->param.puback.message_id==pending_packet_id) {
		if (event->result) { protocol_error=true; } else { acked=true; }
	} else if (event->type==MQTT_EVT_PUBLISH) {
		/* Publisher never subscribes and is not a remote-command receiver. */
		protocol_error=true;
	}
}

static void close_connection(int rc)
{
	socket_io.deadline_ms=k_uptime_get()+IO_MS;
	(void)mqtt_abort(&client);
	/* mqtt_connect can fail before the library owns the socket. */
	int close_rc=xt_socket_close(&socket_io);if (close_rc) { rc=close_rc;atomic_set(&stopping,1); }
	connected=false;connecting=false;pending=false;acked=false;protocol_error=false;
	if (snapshot_active) { ++publisher.counters.snapshots_expired;snapshot_active=false; }
	retry_time=k_uptime_get()+backoff_ms;
	backoff_ms=MIN(backoff_ms*2,10000U);
	connection_error=rc;
	publish_status(rc);
}

static int connect_broker(void)
{
	if (!admit()) { return -ESHUTDOWN; }
	mqtt_client_init(&client);
	broker=(struct net_sockaddr_in){.sin_family=NET_AF_INET,.sin_port=net_htons(bootstrap.port)};
	if (zsock_inet_pton(NET_AF_INET,bootstrap.broker_ipv4,&broker.sin_addr)!=1) { return -EINVAL; }
	unsigned n=0;memcpy(client_id,"xt2-",4);n=4;
	for (unsigned i=0;i<36;++i) { if (bootstrap.identity.vm_id[i]!='-') { client_id[n++]=bootstrap.identity.vm_id[i]; } }client_id[n]=0;
	username=(struct mqtt_utf8){.utf8=(const uint8_t *)bootstrap.username,.size=strlen(bootstrap.username)};
	password=(struct mqtt_utf8){.utf8=(const uint8_t *)bootstrap.password,.size=strlen(bootstrap.password)};
	client.broker=&broker;client.evt_cb=on_event;client.client_id.utf8=(const uint8_t *)client_id;client.client_id.size=n;
	client.user_name=&username;client.password=&password;client.protocol_version=MQTT_VERSION_3_1_1;
	client.transport.type=MQTT_TRANSPORT_CUSTOM;client.transport.custom_transport_data=&socket_io;
	client.rx_buf=rx;client.rx_buf_size=sizeof(rx);client.tx_buf=tx;client.tx_buf_size=sizeof(tx);
	client.clean_session=1;client.keepalive=30;
	socket_io.deadline_ms=k_uptime_get()+IO_MS;
	++publisher.counters.connection_attempts;
	int rc=mqtt_connect(&client);
	if (!rc) { connecting=true;connect_deadline=k_uptime_get()+1000; }
	return rc;
}

static bool budget(size_t bytes)
{
	int64_t now=k_uptime_get();uint64_t refill=(uint64_t)(now-token_time)*BYTES_PER_SECOND/1000;
	if (refill) { tokens=(unsigned)MIN((uint64_t)BURST_BYTES,(uint64_t)tokens+refill);token_time=now; }
	if (bytes>tokens) { return false; }
	tokens-=bytes;return true;
}

static int submit(struct xt_message *message,bool duplicate,uint16_t id)
{
	if (!admit()) { return -ESHUTDOWN; }
	struct mqtt_publish_param param={0};
	param.message.topic.topic.utf8=(const uint8_t *)message->topic;
	param.message.topic.topic.size=strlen(message->topic);param.message.topic.qos=message->qos;
	param.message.payload.data=message->bytes;param.message.payload.len=message->length;
	param.message_id=id;param.retain_flag=message->retain;param.dup_flag=duplicate;
	socket_io.deadline_ms=k_uptime_get()+IO_MS;
	uint64_t start=k_ticks_to_us_floor64(k_uptime_ticks());
	int rc=mqtt_publish(&client,&param);
	publisher.counters.send_us+=k_ticks_to_us_floor64(k_uptime_ticks())-start;
	if (rc) { ++publisher.counters.submit_failures; }
	else { ++publisher.counters.submitted; }
	return rc;
}

static int send_observation(enum xt_kind kind,const struct xt_observation *source,
	const char *sample,const char *snapshot,unsigned part)
{
	if (atomic_get(&stopping)) { return -ESHUTDOWN; }
	bool qos1=kind==XT_INVENTORY || kind==XT_EVENT || kind==XT_THREADS || kind==XT_HISTORY;
	if (qos1 && pending) { return -EBUSY; }
	/* Reserve the maximum packet size before encoding. No live packet waits
	 * offline or ages in a serialized queue; collect again on reconnect. */
	if (!budget(XT_PAYLOAD_MAX+128)) { return -EAGAIN; }
	uint64_t start=k_ticks_to_us_floor64(k_uptime_ticks());
	int rc=xt_encode(&publisher,kind,source,k_uptime_get(),sample,snapshot,part,&outgoing);
	publisher.counters.serialized_us+=k_ticks_to_us_floor64(k_uptime_ticks())-start;
	if (rc) { return rc; }
	/* Return the conservative reservation surplus, with framing still counted. */
	tokens+=(XT_PAYLOAD_MAX+128)-(unsigned)(outgoing.length+strlen(outgoing.topic)+12);
	if (outgoing.qos) {
		inflight=outgoing;pending_kind=kind;
		pending_event_seq=kind==XT_EVENT ? publisher.events[0].event_seq : 0;
		if (!++packet_id) { ++packet_id; }pending_packet_id=packet_id;
		pending=true;acked=false;ack_deadline=k_uptime_get()+ACK_MS;
	}
	rc=submit(outgoing.qos ? &inflight : &outgoing,false,outgoing.qos ? pending_packet_id : 0);
	if (rc) { if (!outgoing.qos) { ++publisher.counters.live_discarded; }close_connection(rc); }
	return rc;
}

static void acknowledge(void)
{
	if (!pending || !acked) { return; }
	++publisher.counters.pubacked;
	if (pending_kind==XT_EVENT && publisher.event_count && publisher.events[0].event_seq==pending_event_seq) { xt_event_acked(&publisher); }
	if (pending_kind==XT_INVENTORY) { inventory_pending=false; }
	if (pending_kind==XT_THREADS || pending_kind==XT_HISTORY) {
		if (++snapshot_part==snapshot_parts) { snapshot_active=false; }
	}
	pending=false;acked=false;
}

static void network_inventory(void)
{
	struct net_if *iface=net_if_get_default();if (!iface) { return; }
	/* Zephyr interface index is an observed identifier, not a Linux eth0 guess. */
	(void)snprintf(interface_name,sizeof(interface_name),"if%d",net_if_get_by_iface(iface));
	publisher.inventory.interface_name=interface_name;
	struct net_linkaddr *link=net_if_get_link_addr(iface);
	if (link && link->len==6) {
		(void)snprintf(publisher.inventory.mac,sizeof(publisher.inventory.mac),"%02x:%02x:%02x:%02x:%02x:%02x",link->addr[0],link->addr[1],link->addr[2],link->addr[3],link->addr[4],link->addr[5]);
	}
	publisher.inventory.ipv4[0]=0;
	if (iface->config.ip.ipv4) {
		for (unsigned i=0;i<NET_IF_MAX_IPV4_ADDR;++i) {
			struct net_if_addr_ipv4 *address=&iface->config.ip.ipv4->unicast[i];
			if (address->ipv4.is_used) { (void)zsock_inet_ntop(NET_AF_INET,&address->ipv4.address.in_addr,publisher.inventory.ipv4,sizeof(publisher.inventory.ipv4));break; }
		}
	}
}

static void freeze_request(void)
{
	if (atomic_get(&stopping)) { return; }
	int request=atomic_get(&snapshot_requested);
	if (!request || snapshot_active || pending) { return; }
	(void)xt_observe(&frozen,request==2);
	if (xt_sample_id(&publisher,frozen_sample) || xt_sample_id(&publisher,frozen_id)) { atomic_set(&snapshot_requested,0);return; }
	snapshot_kind=request==2 ? XT_THREADS : XT_HISTORY;
	if (snapshot_kind==XT_THREADS) { snapshot_parts=(unsigned)((frozen.rows+3)/4); }
	else {
		/* The two Core rings may share one failing boot event. Use model's
		 * first encoded part to determine the unique history page count below. */
		struct xdiag_event unique[16];size_t count=0;
		for (size_t i=0;i<frozen.history.boot_count;++i) { unique[count++]=frozen.history.boot[i]; }
		for (size_t i=0;i<frozen.history.error_entries;++i) { bool found=false;for (size_t j=0;j<count;++j) { if (unique[j].event_seq==frozen.history.errors[i].event_seq) { found=true; } }if (!found) { unique[count++]=frozen.history.errors[i]; } }
		snapshot_parts=(unsigned)((count+7)/8);
	}
	if (!snapshot_parts) { snapshot_parts=1; }
	snapshot_part=0;snapshot_active=true;snapshot_deadline=k_uptime_get()+SNAPSHOT_MS;
	atomic_set(&snapshot_requested,0);
}

static void work(void *a,void *b,void *c)
{
	ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
	memset(&publisher,0,sizeof(publisher));memset(&bootstrap,0,sizeof(bootstrap));
	backoff_ms=1000;retry_time=0;connected=false;connecting=false;pending=false;protocol_error=false;
	snapshot_active=false;tokens=BURST_BYTES;token_time=k_uptime_get();
	bool identified=false;int previous_error=0;int64_t next_capture=0;
	int64_t next_clock=0;
	enum xt_kind periodic[]={XT_HEARTBEAT,XT_MEMORY,XT_NETWORK,XT_PROGRESS,XT_INTERVAL,XT_JITTER,XT_LATENESS,XT_ELAPSED,XT_PUBLISHER,XT_POOLS};
	unsigned periods[]={2000,2000,2000,1000,5000,5000,5000,5000,5000,2000};
	cpu_active=false;cpu_due=0;
	int64_t due[ARRAY_SIZE(periodic)]={0};
	while (!atomic_get(&stopping)) {
		int64_t now=k_uptime_get();
		if (!identified) {
			if (now<retry_time) { k_sleep(K_MSEC(20));continue; }
			memset(&bootstrap,0,sizeof(bootstrap));
			int rc=load_bootstrap(&bootstrap,now+500,loader_context);
			if (atomic_get(&stopping)) { break; }
			if (!rc && (!bootstrap.port || !bootstrap.password[0] ||
				strcmp(bootstrap.username,bootstrap.identity.vm_id))) { rc=-EACCES; }
			if (!rc && !bootstrap.trusted_internal_plaintext) { rc=-ENOTSUP; }
			if (!rc && guest_boot_id[0] && strcmp(guest_boot_id,bootstrap.identity.boot_id)) { rc=-EACCES; }
			if (!rc && !strcmp(previous_publisher_id,bootstrap.identity.publisher_id)) { rc=-EALREADY; }
			if (!rc) {
				inventory.build_id=xt_build_id();
				inventory.cpu_runtime=xdiag_cpu_supported();
				inventory.pools=IS_ENABLED(CONFIG_XENOVA_DIAGNOSTICS_POOLS);
				if(bootstrap.build_id[0]&&strcmp(bootstrap.build_id,inventory.build_id))rc=-EXDEV;
				if(!rc)rc=xt_init(&publisher,&bootstrap.identity,&inventory);
				if(!rc){
				 memcpy(publisher.inventory.display_name,bootstrap.display_name,sizeof(bootstrap.display_name));
				 memcpy(publisher.inventory.artifact_sha256,bootstrap.artifact_sha256,sizeof(bootstrap.artifact_sha256));
				 publisher.inventory.persistent_crash=bootstrap.persistent_crash;
#if defined(CONFIG_NET_HOSTNAME_ENABLE)
				 if(bootstrap.hostname[0])rc=net_hostname_set(bootstrap.hostname,strlen(bootstrap.hostname));
				 if(!rc){const char *name=net_hostname_get();if(name)snprintf(publisher.inventory.hostname,sizeof(publisher.inventory.hostname),"%s",name);}
#endif
				}
			}
			if (rc) {
				connection_error=rc;
				if (rc!=previous_error) { (void)xdiag_error("telemetry-bootstrap",rc);previous_error=rc; }
				publish_status(rc);retry_time=now+1000;k_sleep(K_MSEC(20));continue;
			}
			memcpy(guest_boot_id,bootstrap.identity.boot_id,sizeof(guest_boot_id));
			memcpy(previous_publisher_id,bootstrap.identity.publisher_id,sizeof(previous_publisher_id));
			identified=true;
			printk("XDIAG_BIND %s %s\n",publisher.identity.execution_id,publisher.identity.boot_id);
		}
		if (now>=next_capture) {
			if (collect_current()) { connection_error=-ERANGE;publish_status(connection_error);break; }
			next_capture=now+200;
		}
#if defined(CONFIG_XENOVA_RUNTIME)
		if(now>=next_clock&&!atomic_get(&stopping)){
		 (void)xt_xen_clock_poll(&publisher.identity,&publisher.utc,now+25);
		 next_clock=k_uptime_get()+200;
		}
#endif
		if (!atomic_get(&stopping) && !connected && !connecting && now>=retry_time) {
			int rc=connect_broker();if (rc) { close_connection(rc); }
		}
		if (!atomic_get(&stopping) && (connecting || connected)) {
			socket_io.deadline_ms=k_uptime_get()+IO_MS;
			int rc=mqtt_input(&client);
			if (rc && rc!=-EAGAIN && rc!=-EWOULDBLOCK) { close_connection(rc); }
			if (protocol_error) { close_connection(-EPROTO); }
			if (connecting && k_uptime_get()>=connect_deadline) { close_connection(-ETIMEDOUT); }
		}
		acknowledge();
		if (pending && k_uptime_get()>=ack_deadline) { ++publisher.counters.puback_timeouts;close_connection(-ETIMEDOUT); }
		if (snapshot_active && k_uptime_get()>=snapshot_deadline) { ++publisher.counters.snapshots_expired;snapshot_active=false; }
		if (!atomic_get(&stopping) && connected) {
			connection_error=0;
			socket_io.deadline_ms=k_uptime_get()+IO_MS;
			int rc=mqtt_live(&client);if (rc && rc!=-EAGAIN) { close_connection(rc); }
		}
		if (!atomic_get(&stopping) && connected) {
			if (inventory_pending && !pending) {
				/* Inventory is recollected after CONNACK, including current addresses.
				 * Its wider collection interval must not alter other families' sample. */
				if (collect_current()) { connection_error=-ERANGE;break; }
				uint64_t core_end=current.end_ms;char inventory_sample[37];
				network_inventory();current.end_ms=k_uptime_get();
				if (!xt_sample_id(&publisher,inventory_sample)) { (void)send_observation(XT_INVENTORY,&current,inventory_sample,NULL,0); }
				current.end_ms=core_end;next_capture=k_uptime_get()+200;
			}
			for (unsigned i=0;i<ARRAY_SIZE(periodic) && connected;++i) {
				if (now<due[i] || (periodic[i]>=XT_INTERVAL && periodic[i]<=XT_ELAPSED && current.timing_rc)) { continue; }
				if(periodic[i]==XT_POOLS&&!current.resources.regions)continue;
				int rc=send_observation(periodic[i],&current,current_sample,NULL,0);
				if (rc!=-EAGAIN && rc!=-EBUSY) { due[i]=now+periods[i]; }
			}
			if(cpu_active&&now>=cpu_expiry){cpu_active=false;++publisher.counters.live_discarded;}
			if(!cpu_active&&now>=cpu_due){
			 cpu_frozen=current;memcpy(cpu_sample,current_sample,sizeof(cpu_sample));cpu_part=0;
			 cpu_parts=(unsigned)MAX(1,(cpu_frozen.resources.rows+3)/4);cpu_active=true;
			 cpu_expiry=cpu_frozen.end_ms+2000;cpu_due=now+2000;
			}
			if(cpu_active){
			 int rc=send_observation(XT_CPU,&cpu_frozen,cpu_sample,NULL,cpu_part);
			 if(!rc&&++cpu_part==cpu_parts)cpu_active=false;
			 else if(rc&&rc!=-EAGAIN&&rc!=-EBUSY)cpu_active=false;
			}
			freeze_request();
			if (snapshot_active && !pending) { (void)send_observation(snapshot_kind,&frozen,frozen_sample,frozen_id,snapshot_part); }
			else if (publisher.event_count && !pending && !inventory_pending) { (void)send_observation(XT_EVENT,&current,current_sample,NULL,0); }
		}
		publish_status(connection_error);k_sleep(K_MSEC(20));
	}
	/* No network flush is required for progress/safety. Existing quiesce owns
	 * the deadline; abort closes only this client and drops unsent live data. */
	publisher.counters.stopping=true;
	publisher.counters.events_dropped+=publisher.event_count;publisher.event_count=0;
	if (snapshot_active) { ++publisher.counters.snapshots_expired;snapshot_active=false; }
	if (socket_io.fd>=0) { socket_io.deadline_ms=k_uptime_get()+IO_MS;(void)mqtt_abort(&client); }
	int close_rc=xt_socket_close(&socket_io);if (close_rc) { connection_error=close_rc; }
	memset(bootstrap.password,0,sizeof(bootstrap.password));
	memset(tx,0,sizeof(tx));memset(rx,0,sizeof(rx));
	connected=false;publish_status(connection_error);
	if (!k_mutex_lock(&status_lock,K_MSEC(20))) { status_view.running=false;k_mutex_unlock(&status_lock); }
}

int xt_start(xt_bootstrap_loader loader,void *context,const struct xt_inventory *info)
{
	if (!loader || !info || k_is_in_isr()) { return -EINVAL; }
	if (k_mutex_lock(&api_lock,K_MSEC(20))) { return -EBUSY; }
	if (socket_io.close_error) { k_mutex_unlock(&api_lock);return socket_io.close_error; }
	if (launched && k_thread_join(&worker_thread,K_NO_WAIT)) { k_mutex_unlock(&api_lock);return -EALREADY; }
	if (k_mutex_lock(&status_lock,K_MSEC(20))) { k_mutex_unlock(&api_lock);return -EBUSY; }
	/* A bootstrap-blocked new worker is not absent and must not expose the
	 * previous session's identity/completion view. Authentication stays pending. */
	status_view=(struct xt_status){.running=true,.last_rc=-EAGAIN,.observed_uptime_ms=k_uptime_get()};
	k_mutex_unlock(&status_lock);
	load_bootstrap=loader;loader_context=context;inventory=*info;
	atomic_set(&stopping,0);atomic_set(&snapshot_requested,0);
	launched=true;
	k_thread_create(&worker_thread,xt_worker_stack,K_THREAD_STACK_SIZEOF(xt_worker_stack),work,NULL,NULL,NULL,CONFIG_XENOVA_TELEMETRY_PRIORITY,0,K_NO_WAIT);
	(void)k_thread_name_set(&worker_thread,"telemetry-v2");k_mutex_unlock(&api_lock);return 0;
}

int xt_stop(int64_t deadline)
{
	if (k_current_get()==&worker_thread) { return -EDEADLK; }
	int64_t left=deadline-k_uptime_get();
	if (left<=0) { return -ETIMEDOUT; }
	if (k_mutex_lock(&api_lock,K_MSEC(MIN(20,left)))) { return -EBUSY; }
	if (!launched) { k_mutex_unlock(&api_lock);return socket_io.close_error; }
	k_spinlock_key_t key=k_spin_lock(&admission_lock);
	atomic_set(&stopping,1);
	k_spin_unlock(&admission_lock,key);
	/* Worker never takes api_lock. Hold it through join so a concurrent start
	 * cannot recycle this TCB/stack while an earlier stop still joins it. */
	left=deadline-k_uptime_get();
	int rc=k_thread_join(&worker_thread,left>0?K_MSEC(left):K_NO_WAIT);
	if (!rc) { launched=false; }
	k_mutex_unlock(&api_lock);return rc?-ETIMEDOUT:socket_io.close_error;
}

int xt_snapshot_request(bool threads)
{
	struct xt_status state;int rc=xt_status_get(&state);if (rc) { return rc; }
	if (!state.running || !state.identified || !state.connected) { return -ENOTCONN; }
	return atomic_cas(&snapshot_requested,0,threads ? 2 : 1) ? 0 : -EBUSY;
}

int xt_status_get(struct xt_status *out)
{
	if (!out || k_is_in_isr()) { return -EINVAL; }
	if (k_mutex_lock(&status_lock,K_MSEC(20))) { return -EBUSY; }
	*out=status_view;k_mutex_unlock(&status_lock);return 0;
}
