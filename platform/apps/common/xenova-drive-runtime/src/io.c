/* SPDX-License-Identifier: Apache-2.0 */
/* Bootstrap, v1 decoder, socket and envelope sequence belong to this worker.
 * The calculation owns payload parsers and never waits for this worker's I/O. */
#include <xenova/drive_io.h>
#include <xenova/diagnostics.h>
#include <zephyr/sys/atomic.h>
#include <errno.h>
#include <string.h>

K_MUTEX_DEFINE(exchange_lock);
K_MUTEX_DEFINE(io_lifecycle_lock);
K_SEM_DEFINE(io_wake,0,1);
K_THREAD_STACK_DEFINE(io_stack,8192);
static struct k_thread thread;
static atomic_t stop;
static struct xm_zclient *client;
static char control_topic[XM_TOPIC_MAX], input_topic[XM_TOPIC_MAX], perception_topic[XM_TOPIC_MAX];
static bool dual_mode, perception_registered;
static struct xd_inputs shared, working;
static struct xd_candidate pending, active;
static bool command_pending, initialized, attempted_start;
static struct xd_io_stats stats;
static atomic_t snapshot_busy;
static atomic_t clock_error;
struct report {
 char topic[XM_TOPIC_MAX], type[32], json[160];
 int64_t expires_ms;
 uint64_t control_revision, connection_revision, completed;
};
static struct report heartbeat[2], events[4], report_active;
static bool heartbeat_pending[2];
static unsigned heartbeat_cursor;
static bool last_report_was_event;
static unsigned event_head,event_count;

static uint64_t clock_us(void) {
 uint64_t value;int rc=xdiag_clock_us(&value);
 if(rc){atomic_set(&clock_error,rc);atomic_set(&stop,1);return UINT64_MAX;}
 return value;
}
static struct xd_delivery result(const struct xd_candidate *c,enum xd_disposition why,int rc) {
 return (struct xd_delivery){.id=c->id,.created_us=c->created_us,.queued_us=c->queued_us,
  .taken_us=clock_us(),.capture_us=c->capture_us,.rc=rc,.disposition=why};
}
static bool same_control_context(const struct xd_received *a,const struct xd_received *b) {
 /* A fresh envelope of the same control state is not a new generation.
  * Keep its actual seq/rx timestamp in the snapshot, without invalidating
  * candidates unless state, validity or sender execution really changed. */
 return a->error==b->error&&a->length==b->length&&!memcmp(a->payload,b->payload,a->length)&&
  !strcmp(a->sender,b->sender)&&!strcmp(a->boot,b->boot)&&!strcmp(a->instance,b->instance);
}
static void receive(const char *topic,const char *type,struct xd_received *out) {
 struct xm_envelope env;int64_t age;
 memset(out,0,sizeof(*out));out->seq=-1;
 out->error=xm_zclient_latest(client,topic,&env,&age);
 if(out->error)return;
 /* rx_ms is the actual accepted receive time, not time of copying/decoding. */
 for(size_t i=0;i<ARRAY_SIZE(client->latest);i++) {
  if(!strcmp(client->latest[i].topic,topic)){out->received_ms=client->latest[i].rx_ms;break;}
 }
 out->seq=env.seq;
 if(!xm_span_eq(env.type,type)||env.payload.len>XM_MAX_DRIVING_BYTES) {out->error=-EBADMSG;return;}
 out->length=env.payload.len;memcpy(out->payload,env.payload.p,out->length);
 if(xm_span_copy(out->sender,sizeof(out->sender),env.vm_id.p?env.vm_id:env.source_id)<0||
    xm_span_copy(out->boot,sizeof(out->boot),env.boot_id)<0||
    xm_span_copy(out->instance,sizeof(out->instance),env.instance_id)<0) {
  out->length=0;out->error=-EOVERFLOW;
 }
}
static void refresh(void) {
 receive(control_topic,"vehicle.control",&working.control);
 receive(input_topic,dual_mode||(client->id.app_id&&!strcmp(client->id.app_id,"manual"))?"drive.input":"perception.result",&working.input);
 if(dual_mode)receive(perception_topic,"perception.result",&working.perception);
 working.dual_mode=dual_mode;
 if(client->id.vm_id&&strlen(client->id.vm_id)==XM_UUID_LEN)strcpy(working.vm_id,client->id.vm_id);
 working.connected=client->connected;
 k_mutex_lock(&exchange_lock,K_FOREVER);
 if(!same_control_context(&working.control,&shared.control))shared.control_revision++;
 if(working.connected!=shared.connected)shared.connection_revision++;
 working.control_revision=shared.control_revision;
 working.connection_revision=shared.connection_revision;
 shared=working;
 k_mutex_unlock(&exchange_lock);
}
static bool context_ok(uint64_t control,uint64_t connection) {
 return shared.connected&&shared.control_revision==control&&shared.connection_revision==connection;
}
static void send_command(void) {
 k_mutex_lock(&exchange_lock,K_FOREVER);
 bool take=command_pending;
 if(take){active=pending;command_pending=false;}
 bool valid=take&&context_ok(active.control_revision,active.connection_revision);
 k_mutex_unlock(&exchange_lock);
 if(!take)return;
 struct xd_delivery d=result(&active,XD_SENT,0);
 if(atomic_get(&stop)||!xd_owner_admit()){d.disposition=XD_STOPPED;d.rc=-ESHUTDOWN;}
 else if(!valid){d.disposition=XD_CONTEXT_CHANGED;d.rc=-EXDEV;}
 else if(k_uptime_get()>=active.expires_ms){d.disposition=XD_EXPIRED;d.rc=-ETIME;}
 else {
  d.send_begin_us=clock_us();
  uint32_t before=client->message_counter;
  d.rc=xm_zclient_publish_until(client,active.topic,"drive.command",active.json,active.expires_ms);
  d.send_end_us=clock_us();
  d.disposition=d.rc?XD_SEND_FAILED:XD_SENT;
  if(before!=client->message_counter)d.message=client->message_counter;
  if(!d.rc)for(size_t i=0;i<ARRAY_SIZE(client->pubs);i++)if(!strcmp(client->pubs[i].topic,active.topic)){d.topic_seq=client->pubs[i].seq;break;}
 }
 k_mutex_lock(&exchange_lock,K_FOREVER);
 if(d.disposition==XD_CONTEXT_CHANGED)stats.context_dropped++;
 else if(d.disposition==XD_EXPIRED)stats.expired++;
 else if(d.disposition==XD_STOPPED)stats.stopped++;
 else {stats.attempted++;if(d.rc)stats.failed++;else stats.sent++;}
 k_mutex_unlock(&exchange_lock);
 xd_delivery_record(&d);
}
static void send_report(void) {
 k_mutex_lock(&exchange_lock,K_FOREVER);
 unsigned slot=heartbeat_cursor;
 if(!heartbeat_pending[slot])slot=1-slot;
 bool pending_beat=heartbeat_pending[slot];
 bool take=event_count||pending_beat;
 /* Once a heartbeat is pending, at most one event may pass it. The payload
  * still comes only from a completed calculation; this owner never renews it. */
 bool beat=pending_beat&&(!event_count||last_report_was_event);
 if(beat){report_active=heartbeat[slot];heartbeat_pending[slot]=false;heartbeat_cursor=1-slot;last_report_was_event=false;}
 else if(event_count){report_active=events[event_head];event_head=(event_head+1)%ARRAY_SIZE(events);event_count--;last_report_was_event=true;}
 bool valid=take&&context_ok(report_active.control_revision,report_active.connection_revision);
 k_mutex_unlock(&exchange_lock);
 if(!take)return;
 bool expired=valid&&k_uptime_get()>=report_active.expires_ms;
 int rc=expired?-ETIME:-EXDEV;
 if(valid&&!expired&&!atomic_get(&stop)&&xd_owner_admit())
  rc=xm_zclient_publish_until(client,report_active.topic,report_active.type,report_active.json,report_active.expires_ms);
 if(rc){
  k_mutex_lock(&exchange_lock,K_FOREVER);
  if(beat){stats.heartbeat_dropped++;if(expired||(rc==-ETIMEDOUT&&k_uptime_get()>=report_active.expires_ms))stats.heartbeat_expired++;}
  else stats.event_dropped++;
  k_mutex_unlock(&exchange_lock);
 }
}
static void owner(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 while(!atomic_get(&stop)) {
  uint64_t before=clock_us();
  if(atomic_get(&stop)||!xd_owner_admit())break;
  int rc=xd_owner_prepare(client,control_topic,input_topic);
  if(!rc&&dual_mode&&!perception_registered){
   rc=xm_zclient_subscribe(client,perception_topic,0);
   perception_registered=!rc;
  }
  uint64_t after=clock_us();
  k_mutex_lock(&exchange_lock,K_FOREVER);stats.prepare_max_us=MAX(stats.prepare_max_us,after-before);k_mutex_unlock(&exchange_lock);
  if(rc){(void)k_sem_take(&io_wake,K_MSEC(10));continue;}
  initialized=true;
  if(atomic_get(&stop)||!xd_owner_admit())break;
  /* Probe socket readiness without an idle wait. Frame reads/connect still
   * have the original bounded I/O budget. Idle below is mailbox-wakeable. */
  before=clock_us();(void)xm_zclient_poll(client,0);after=clock_us();
  k_mutex_lock(&exchange_lock,K_FOREVER);stats.poll_max_us=MAX(stats.poll_max_us,after-before);k_mutex_unlock(&exchange_lock);
  refresh();
  if(atomic_get(&stop)||!xd_owner_admit())break;
  send_command();send_report();
  (void)k_sem_take(&io_wake,K_MSEC(10));
 }
 if(initialized)xm_zclient_close(client);
 k_mutex_lock(&exchange_lock,K_FOREVER);
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
 if(initialized)stats.close_error=client->socket_io.close_error;
#endif
 struct xd_delivery d;
 bool discarded=command_pending;
 if(discarded){d=result(&pending,XD_STOPPED,-ESHUTDOWN);command_pending=false;stats.stopped++;}
 stats.event_dropped+=event_count;
 for(unsigned i=0;i<ARRAY_SIZE(heartbeat);i++){stats.heartbeat_dropped+=heartbeat_pending[i];heartbeat_pending[i]=false;}
 event_count=0;
 shared.connected=false;
 k_mutex_unlock(&exchange_lock);
 if(discarded)xd_delivery_record(&d);
}
static int start(struct xm_zclient *c,const char *control,const char *input,const char *perception) {
 if(!c||!control||!input||strlen(control)>=sizeof(control_topic)||strlen(input)>=sizeof(input_topic))return -EINVAL;
 if(perception&&strlen(perception)>=sizeof(perception_topic))return -EINVAL;
 if(atomic_get(&stop))return -ESHUTDOWN;
 if(k_mutex_lock(&io_lifecycle_lock,K_NO_WAIT))return -EBUSY;
 int rc=0;
 if(attempted_start) {
  if(c!=client||strcmp(control,control_topic)||strcmp(input,input_topic)||
     (perception!=NULL)!=dual_mode||(perception&&strcmp(perception,perception_topic)))rc=-EINVAL;
  else if(!stats.started)rc=-EIO;
 } else {
  attempted_start=true;client=c;strcpy(control_topic,control);strcpy(input_topic,input);
  dual_mode=perception!=NULL;if(perception)strcpy(perception_topic,perception);
  stats.started=k_thread_create(&thread,io_stack,K_THREAD_STACK_SIZEOF(io_stack),owner,NULL,NULL,NULL,CONFIG_XENOVA_DRIVE_IO_PRIORITY,0,K_NO_WAIT)!=NULL;
  if(!stats.started)rc=-ENOMEM;
  else (void)k_thread_name_set(&thread,"drive-v1");
 }
 k_mutex_unlock(&io_lifecycle_lock);return rc;
}
int xd_io_start(struct xm_zclient *c,const char *control,const char *input) {
 return start(c,control,input,NULL);
}
int xd_io_start_dual(struct xm_zclient *c,const char *control,const char *input,const char *perception) {
 if(!perception)return -EINVAL;
 return start(c,control,input,perception);
}
int xd_io_snapshot(struct xd_inputs *out) {
 if(!out)return -EINVAL;
 if(k_mutex_lock(&exchange_lock,K_NO_WAIT)){atomic_inc(&snapshot_busy);return -EBUSY;}
 uint64_t before=clock_us();*out=shared;
 stats.exchange_max_us=MAX(stats.exchange_max_us,clock_us()-before);
 k_mutex_unlock(&exchange_lock);return 0;
}
int xd_io_command(struct xd_candidate *c) {
 if(!c||!c->id||strnlen(c->json,sizeof(c->json))==sizeof(c->json)||strnlen(c->topic,sizeof(c->topic))==sizeof(c->topic))return -EINVAL;
 if(atomic_get(&stop))return -ESHUTDOWN;
 if(k_mutex_lock(&exchange_lock,K_NO_WAIT))return -EBUSY;
 int rc=0;struct xd_delivery old;
 bool replaced=false;
 if(atomic_get(&stop))rc=-ESHUTDOWN;
 else if(!context_ok(c->control_revision,c->connection_revision))rc=-EXDEV;
 else {
  if(command_pending){old=result(&pending,XD_REPLACED,-EALREADY);stats.replaced++;replaced=true;}
  c->queued_us=clock_us();pending=*c;command_pending=true;stats.enqueued++;
 }
 k_mutex_unlock(&exchange_lock);
 if(replaced)xd_delivery_record(&old);
 if(!rc)k_sem_give(&io_wake);
 return rc;
}
int xd_io_report(const char *topic,const char *type,const char *json,uint64_t completed,const struct xd_inputs *context) {
 if(!topic||!type||!json||!context||!completed||strlen(topic)>=XM_TOPIC_MAX||strlen(type)>=32||strlen(json)>=160)return -EINVAL;
 bool beat=!strcmp(type,"app.heartbeat");
 if(!beat&&strcmp(type,"perception.event"))return -ENOTSUP;
 if(atomic_get(&stop))return -ESHUTDOWN;
 if(k_mutex_lock(&exchange_lock,K_NO_WAIT))return -EBUSY;
 int rc=0;
 if(atomic_get(&stop))rc=-ESHUTDOWN;
 else if(!context_ok(context->control_revision,context->connection_revision))rc=-EXDEV;
 else if(!beat&&event_count==ARRAY_SIZE(events)){stats.event_dropped++;rc=-ENOBUFS;}
 else {
  unsigned slot=0;
  if(beat&&dual_mode){
   /* Fixed logical producers, not an unbounded topic-indexed queue. */
   if(!strcmp(topic,"xenova/v1/apps/autonomous/events"))slot=1;
   else if(strcmp(topic,"xenova/v1/apps/manual/events")){k_mutex_unlock(&exchange_lock);return -EINVAL;}
  }
  struct report *r=beat?&heartbeat[slot]:&events[(event_head+event_count++)%ARRAY_SIZE(events)];
  if(beat){stats.heartbeat_replaced+=heartbeat_pending[slot];heartbeat_pending[slot]=true;}
  strcpy(r->topic,topic);strcpy(r->type,type);strcpy(r->json,json);
  r->completed=completed;r->control_revision=context->control_revision;r->connection_revision=context->connection_revision;
  r->expires_ms=k_uptime_get()+1000;
 }
 k_mutex_unlock(&exchange_lock);return rc;
}
void xd_io_stop_request(void){atomic_set(&stop,1);k_sem_give(&io_wake);}
int xd_io_join(int64_t end) {
 xd_io_stop_request();
 if(k_current_get()==&thread)return -EDEADLK;
 int64_t left=end-k_uptime_get();
 if(left<=0||k_mutex_lock(&io_lifecycle_lock,K_MSEC(left)))return -ETIMEDOUT;
 int rc=0;
 if(stats.started&&!stats.joined){
  left=end-k_uptime_get();rc=k_thread_join(&thread,left>0?K_MSEC(left):K_NO_WAIT);
  if(rc==-EAGAIN||rc==-EBUSY)rc=-ETIMEDOUT;
  if(!rc)stats.joined=true;
 }
 if(!rc)rc=stats.close_error;
 if(!rc)rc=atomic_get(&clock_error);
 k_mutex_unlock(&io_lifecycle_lock);return rc;
}
int xd_io_stats_get(struct xd_io_stats *out) {
 if(!out)return -EINVAL;
 if(k_mutex_lock(&io_lifecycle_lock,K_NO_WAIT))return -EBUSY;
 if(k_mutex_lock(&exchange_lock,K_NO_WAIT)){k_mutex_unlock(&io_lifecycle_lock);return -EBUSY;}
 *out=stats;out->snapshot_busy=(uint32_t)atomic_get(&snapshot_busy);
 out->clock_error=atomic_get(&clock_error);
 out->stack_error=-ENODATA;
#if defined(CONFIG_THREAD_STACK_INFO) && defined(CONFIG_INIT_STACKS)
 if(stats.started&&stats.joined)out->stack_error=k_thread_stack_space_get(&thread,&out->stack_unused_bytes);
#else
 out->stack_error=-ENOTSUP;
#endif
 k_mutex_unlock(&exchange_lock);k_mutex_unlock(&io_lifecycle_lock);return 0;
}
