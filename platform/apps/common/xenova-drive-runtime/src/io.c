/* SPDX-License-Identifier: Apache-2.0 */
/* Bootstrap, v1 decoder, socket and envelope sequence belong to this worker.
 * The calculation owns payload parsers and never waits for this worker's I/O. */
#include <xenova/drive_io.h>
#include <xenova/diagnostics.h>
#include <xenova/auto_delay.h>
#include <xenova/memory_stress.h>
#include <xenova/cpu_stress.h>
#include <zephyr/sys/atomic.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

K_MUTEX_DEFINE(exchange_lock);
K_MUTEX_DEFINE(io_lifecycle_lock);
K_SEM_DEFINE(io_wake,0,1);
K_THREAD_STACK_DEFINE(io_stack,8192);
static struct k_thread thread;
static atomic_t stop;
static struct xm_zclient *client;
static char control_topic[XM_TOPIC_MAX], input_topic[XM_TOPIC_MAX], perception_topic[XM_TOPIC_MAX];
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
static char fault_command_topic[XM_TOPIC_MAX],fault_result_topic[XM_TOPIC_MAX];
static bool fault_command_registered,fault_running;
static int64_t fault_last_rx=-1,fault_started_ms,fault_deadline_ms;
static struct xm_auto_delay_command fault_command;
static char fault_last_status[12];
static struct xm_resource_stress_command resource_command;
static bool resource_running;
static int64_t resource_started_ms;
static uint64_t resource_duration_ms;
static char resource_last_status[12];
#endif
static bool dual_mode, perception_registered;
static struct xm_zbinary binary;
static bool binary_registered;
static struct xd_inputs shared, working;
static struct xd_manual_source manual_source; /* v1 receive owner only */
/* The I/O owner publishes only semantic metadata with the existing exchange.
 * Never read live manual_source or transport payloads from Diagnostics. */
static struct xm_vehicle_control memory_control;
static int memory_control_rc=-ENODATA;
static struct {
 int64_t sequence,received_ms;
 uint64_t context_revision,connection_revision;
 uint32_t generation,age_ms;
 char session[XM_UUID_LEN+1],receiver[XM_UUID_LEN+1];
 bool classified,valid,committed;
} memory_source;
static struct xdr_app_buffer input_memory;
static uint32_t input_age_ms;
static uint64_t input_peak;
static struct xd_candidate pending, active;
static bool command_pending, initialized, attempted_start;
static struct xd_io_stats stats;
static atomic_t snapshot_busy;
static atomic_t clock_error;
struct report {
 char topic[XM_TOPIC_MAX], type[32], json[256], app_instance[XM_UUID_LEN+1];
 int64_t expires_ms;
 uint64_t control_revision, connection_revision, completed;
};
/* heartbeat and readiness have separate per-app slots. A fresh heartbeat must
 * not renew an old readiness report or replace its actual progress. */
static struct report heartbeat[4], events[4], report_active;
static bool heartbeat_pending[4];
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
static int accept_input(void *unused,const char *topic,const struct xm_envelope *env,int64_t now)
{
 ARG_UNUSED(unused);
 if(!strcmp(topic,control_topic)&&xm_span_eq(env->type,"vehicle.control"))
  memory_control_rc=xd_manual_control_observed(&manual_source,env->payload,&memory_control);
 if(!strcmp(topic,input_topic)&&xm_span_eq(env->type,"drive.input")){
  bool seen=manual_source.seen;uint64_t sample=manual_source.last.sample;
  char session[XM_UUID_LEN+1];strcpy(session,manual_source.last.session);
  int rc=xd_manual_accept(&manual_source,env->payload,now);
  if(!rc){
   /* A legacy body does not update the parsed original sample. Never label
    * it using the previous strict sample, even in the same clock tick. */
   memory_source.classified=manual_source.seen&&(!seen||sample!=manual_source.last.sample||
    strcmp(session,manual_source.last.session));
   memory_source.sequence=env->seq;memory_source.received_ms=now;memory_source.committed=false;
   memory_source.connection_revision=working.connection_revision+(client->connected!=working.connected);
   memory_source.valid=manual_source.last.valid;memory_source.generation=manual_source.last.generation;
   memory_source.age_ms=manual_source.last.age_ms;
   strcpy(memory_source.session,manual_source.last.session);strcpy(memory_source.receiver,manual_source.receiver);
  }
  return rc;
 }
 return 0;
}
/* Existing exchange lock held; called only at the actual full input commit. */
static void memory_commit(void){
 const struct xd_received *in=&working.input;
 bool original=in->length&&memory_source.sequence==in->seq&&memory_source.received_ms==in->received_ms;
 if(original&&!memory_source.committed){
  memory_source.context_revision=working.control_revision;memory_source.committed=true;
 }
 input_memory=(struct xdr_app_buffer){.content_bytes=in->length,.sequence=in->seq<0?0:(uint64_t)in->seq,
  .source_ms=in->received_ms,.source_rc=in->error,.context_revision=original?memory_source.context_revision:0,
  .connection_revision=original?memory_source.connection_revision:0,.phase=in->length?XDR_BUFFER_STORED:XDR_BUFFER_EMPTY,
  .input_status=XDR_INPUT_MISSING};
 input_peak=MAX(input_peak,in->length);input_age_ms=memory_source.age_ms;
 if(!in->length){if(!input_memory.source_rc)input_memory.source_rc=-ENODATA;return;}
 input_memory.input_status=XDR_INPUT_UNCLASSIFIED;
 if(!memory_source.classified||!original)return;
 if(working.control.error||memory_control_rc||!working.connected||working.connection_revision!=memory_source.connection_revision||
    strcmp(memory_source.receiver,working.app_instances[1])||strcmp(memory_control.mode,"MANUAL")||
    memory_control.generation!=memory_source.generation||strcmp(memory_control.manual_session,memory_source.session)||
    (memory_control.active_vm_id[0]&&strcmp(memory_control.active_vm_id,working.vm_id))||
    strcmp(memory_control.active_app,"manual"))input_memory.input_status=XDR_INPUT_CONTEXT_CHANGED;
 else input_memory.input_status=!memory_source.valid||manual_source.blocked?XDR_INPUT_INVALID:XDR_INPUT_ACCEPTED;
}
static void refresh(void) {
 receive(control_topic,"vehicle.control",&working.control);
 receive(input_topic,dual_mode||(client->id.app_id&&!strcmp(client->id.app_id,"manual"))?"drive.input":"perception.result",&working.input);
 if(dual_mode)receive(perception_topic,"perception.result",&working.perception);
 working.manual_witness=manual_source.witness;
 working.dual_mode=dual_mode;
 if(client->id.vm_id&&strlen(client->id.vm_id)==XM_UUID_LEN)strcpy(working.vm_id,client->id.vm_id);
 working.connected=client->connected;
#if defined(CONFIG_XENOVA_DRIVE_WARM)
 xd_owner_context(&working);
#endif
 k_mutex_lock(&exchange_lock,K_FOREVER);
 if(!same_control_context(&working.control,&shared.control))shared.control_revision++;
 if(working.connected!=shared.connected)shared.connection_revision++;
 working.control_revision=shared.control_revision;
 working.connection_revision=shared.connection_revision;
 memory_commit();
 shared=working;
 k_mutex_unlock(&exchange_lock);
}
static bool context_ok(uint64_t control,uint64_t connection) {
 return shared.connected&&shared.control_revision==control&&shared.connection_revision==connection;
}
static int app_index(const char *topic){struct xm_topic_info info;xm_topic_parse(topic,&info);
 return !strcmp(info.app_id,"autonomous")?0:!strcmp(info.app_id,"manual")?1:-1;}
static bool app_ok(const char *topic,const char *instance){
#if defined(CONFIG_XENOVA_DRIVE_WARM)
 int index=app_index(topic);
 return index>=0&&instance[0]&&!strcmp(instance,shared.app_instances[index])&&xd_owner_instance(index,instance);
#else
 ARG_UNUSED(topic);ARG_UNUSED(instance);return true;
#endif
}
static int publish_as(const char *topic,const char *type,const char *json,const char *instance,int64_t end){
#if defined(CONFIG_XENOVA_DRIVE_WARM)
 return xm_zclient_publish_as_until(client,topic,type,json,instance,end);
#else
 ARG_UNUSED(instance);return xm_zclient_publish_until(client,topic,type,json,end);
#endif
}
static void send_command(void) {
 k_mutex_lock(&exchange_lock,K_FOREVER);
 bool take=command_pending;
 if(take){active=pending;command_pending=false;}
 bool valid=take&&context_ok(active.control_revision,active.connection_revision)&&app_ok(active.topic,active.app_instance);
 k_mutex_unlock(&exchange_lock);
 if(!take)return;
 struct xd_delivery d=result(&active,XD_SENT,0);
 if(atomic_get(&stop)||!xd_owner_admit()){d.disposition=XD_STOPPED;d.rc=-ESHUTDOWN;}
 else if(!valid){d.disposition=XD_CONTEXT_CHANGED;d.rc=-EXDEV;}
 else if(k_uptime_get()>=active.expires_ms){d.disposition=XD_EXPIRED;d.rc=-ETIME;}
 else {
  d.send_begin_us=clock_us();
  uint32_t before=client->message_counter;
  d.rc=publish_as(active.topic,"drive.command",active.json,active.app_instance,active.expires_ms);
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
 for(unsigned i=0;i<ARRAY_SIZE(heartbeat)&&!heartbeat_pending[slot];i++)slot=(slot+1)%ARRAY_SIZE(heartbeat);
 bool pending_beat=heartbeat_pending[slot];
 bool take=event_count||pending_beat;
 /* Once a heartbeat is pending, at most one event may pass it. The payload
  * still comes only from a completed calculation; this owner never renews it. */
 bool beat=pending_beat&&(!event_count||last_report_was_event);
 if(beat){report_active=heartbeat[slot];heartbeat_pending[slot]=false;heartbeat_cursor=(slot+1)%ARRAY_SIZE(heartbeat);last_report_was_event=false;}
 else if(event_count){report_active=events[event_head];event_head=(event_head+1)%ARRAY_SIZE(events);event_count--;last_report_was_event=true;}
 bool valid=take&&context_ok(report_active.control_revision,report_active.connection_revision)&&app_ok(report_active.topic,report_active.app_instance);
 k_mutex_unlock(&exchange_lock);
 if(!take)return;
 bool expired=valid&&k_uptime_get()>=report_active.expires_ms;
 int rc=expired?-ETIME:-EXDEV;
 if(valid&&!expired&&!atomic_get(&stop)&&xd_owner_admit())
  rc=publish_as(report_active.topic,report_active.type,report_active.json,report_active.app_instance,report_active.expires_ms);
 bool lifecycle=beat&&!strcmp(report_active.type,"app.lifecycle");
 if(rc||lifecycle){
  k_mutex_lock(&exchange_lock,K_FOREVER);
  if(!rc)stats.lifecycle_sent++;
  else if(beat){
   if(lifecycle&&!stats.lifecycle_first_send_error)stats.lifecycle_first_send_error=rc;
   bool readiness=!strcmp(report_active.type,"app.readiness")||!strcmp(report_active.type,"app.lifecycle");
   bool late=expired||(rc==-ETIMEDOUT&&k_uptime_get()>=report_active.expires_ms);
   if(readiness){stats.readiness_dropped++;stats.readiness_expired+=late;}
   else {stats.heartbeat_dropped++;stats.heartbeat_expired+=late;}
  }
  else stats.event_dropped++;
  k_mutex_unlock(&exchange_lock);
 }
}
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
static void fault_result(const struct xm_auto_delay_command *command,const char *status,const char *error,uint64_t duration)
{
 char json[512];if(!command->command_id[0]||xm_auto_delay_result_json(json,sizeof(json),command,status,duration,error)<0)return;
 if(command==&fault_command)strcpy(fault_last_status,status);
 (void)xm_zclient_publish(client,fault_result_topic,"agent.command_result",json);
}
static void resource_stress_result(const struct xm_resource_stress_command *command,const char *status,const char *error,uint64_t duration)
{
 char json[512];if(!command->command_id[0]||xm_resource_stress_result_json(json,sizeof(json),command,status,duration,error)<0)return;
 if(command==&resource_command)strcpy(resource_last_status,status);
 (void)xm_zclient_publish(client,fault_result_topic,"agent.command_result",json);
}
static bool same_fault_command(const struct xm_auto_delay_command *a,const struct xm_auto_delay_command *b)
{
 return !strcmp(a->command_id,b->command_id)&&!strcmp(a->operation_id,b->operation_id)&&
  !strcmp(a->expected_boot_id,b->expected_boot_id)&&a->delay_ms==b->delay_ms&&a->duration_s==b->duration_s;
}
static bool same_resource_command(const struct xm_resource_stress_command *a,const struct xm_resource_stress_command *b)
{
 return !strcmp(a->operation_id,b->operation_id)&&!strcmp(a->expected_boot_id,b->expected_boot_id)&&
  a->action==b->action&&a->workers==b->workers&&a->load_percent==b->load_percent&&
  a->memory_mib==b->memory_mib&&a->duration_s==b->duration_s;
}
static void receive_fault_command(void)
{
 struct xm_envelope env;int64_t age;
 if(xm_zclient_latest(client,fault_command_topic,&env,&age))return;
 int64_t rx=0;for(size_t i=0;i<ARRAY_SIZE(client->latest);i++)if(!strcmp(client->latest[i].topic,fault_command_topic)){rx=client->latest[i].rx_ms;break;}
 if(rx==fault_last_rx)return;fault_last_rx=rx;
 if(!xm_span_eq(env.type,"agent.command"))return;
 struct xm_resource_stress_command resource;int resource_rc=xm_resource_stress_command_parse(env.payload,&resource);
 if(resource_rc!=-ENOTSUP){
  if(xm_zclient_latest_retained(client,fault_command_topic)){resource_stress_result(&resource,"REJECTED","INVALID_COMMAND",0);return;}
  if(resource_rc){resource_stress_result(&resource,"REJECTED",resource_rc==-ERANGE?"INVALID_PARAMETERS":"INVALID_COMMAND",0);return;}
  if(strcmp(resource.expected_boot_id,client->id.boot_id)){resource_stress_result(&resource,"REJECTED","BOOT_MISMATCH",0);return;}
  if(resource_command.command_id[0]&&!strcmp(resource.command_id,resource_command.command_id)){
   bool same=same_resource_command(&resource,&resource_command);
   if(!same)resource_stress_result(&resource,"REJECTED","IDEMPOTENCY_CONFLICT",0);
   else resource_stress_result(&resource_command,resource_last_status[0]?resource_last_status:(resource_running?"RUNNING":"FAILED"),NULL,resource_running?0:resource_duration_ms);
   return;
  }
  if(resource_running){resource_stress_result(&resource,"REJECTED","BUSY",0);return;}
  int start_rc=resource.action==XM_CPU_STRESS?
   drive_cpu_stress_start(resource.workers,resource.load_percent,resource.duration_s):
   drive_memory_stress_start(resource.memory_mib,resource.duration_s);
  if(start_rc){const char *error=start_rc==-EBUSY?"BUSY":(start_rc==-EINVAL?"INVALID_PARAMETERS":"RESOURCE_UNAVAILABLE");resource_stress_result(&resource,"REJECTED",error,0);return;}
  resource_command=resource;resource_running=true;resource_duration_ms=0;resource_started_ms=k_uptime_get();resource_stress_result(&resource_command,"RUNNING",NULL,0);return;
 }
 struct xm_auto_delay_command incoming;int rc=xm_auto_delay_command_parse(env.payload,&incoming);
 if(xm_zclient_latest_retained(client,fault_command_topic)){fault_result(&incoming,"REJECTED","INVALID_COMMAND",0);return;}
 if(rc){fault_result(&incoming,"REJECTED",rc==-ERANGE?"INVALID_PARAMETERS":"INVALID_COMMAND",0);return;}
 if(strcmp(incoming.expected_boot_id,client->id.boot_id)){fault_result(&incoming,"REJECTED","BOOT_MISMATCH",0);return;}
 if(fault_command.command_id[0]&&!strcmp(incoming.command_id,fault_command.command_id)){
  if(!same_fault_command(&incoming,&fault_command))fault_result(&incoming,"REJECTED","IDEMPOTENCY_CONFLICT",0);
  else fault_result(&fault_command,fault_last_status[0]?fault_last_status:(fault_running?"RUNNING":"FAILED"),NULL,
                    fault_running?0:(uint64_t)MAX(k_uptime_get()-fault_started_ms,0));
  return;
 }
 rc=drive_auto_delay_start(incoming.delay_ms,incoming.duration_s);
 if(rc){fault_result(&incoming,"REJECTED",rc==-EBUSY?"BUSY":"STRESS_FAILED",0);return;}
 fault_command=incoming;fault_running=true;fault_started_ms=k_uptime_get();
 fault_deadline_ms=fault_started_ms+(int64_t)incoming.duration_s*1000;fault_result(&fault_command,"RUNNING",NULL,0);
}
static void observe_resource_stress(void)
{
 if(!resource_running)return;
 bool running=false,succeeded=false;int error=0;
 if(resource_command.action==XM_CPU_STRESS){
  struct drive_cpu_stress_status status;drive_cpu_stress_status_get(&status);
  running=status.state==DRIVE_CPU_STRESS_RUNNING;succeeded=status.state==DRIVE_CPU_STRESS_SUCCEEDED;error=status.error;
 }else{
  struct drive_memory_stress_status status;drive_memory_stress_status_get(&status);
  running=status.state==DRIVE_MEMORY_STRESS_RUNNING;succeeded=status.state==DRIVE_MEMORY_STRESS_SUCCEEDED;error=status.error;
 }
 if(running)return;resource_running=false;resource_duration_ms=(uint64_t)MAX(k_uptime_get()-resource_started_ms,0);
 if(succeeded)resource_stress_result(&resource_command,"SUCCEEDED",NULL,resource_duration_ms);
 else resource_stress_result(&resource_command,"FAILED",error==-ENOMEM?"RESOURCE_UNAVAILABLE":"STRESS_FAILED",resource_duration_ms);
}
static void observe_fault_command(void)
{
 if(!fault_running)return;
 struct drive_auto_delay_status status;drive_auto_delay_status_get(&status);int64_t now=k_uptime_get();
 /* Actual AUTO completion/deadline health owns the per-app restriction.
  * Injector progress and heap occupancy cannot declare a VM-wide failure. */
 if(now>=fault_deadline_ms){fault_running=false;fault_result(&fault_command,"SUCCEEDED",NULL,(uint64_t)(now-fault_started_ms));}
 else if(!status.active){fault_running=false;fault_result(&fault_command,"FAILED","STRESS_FAILED",(uint64_t)(now-fault_started_ms));}
}
#endif
static void owner(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 while(!atomic_get(&stop)) {
  uint64_t before=clock_us();
  if(atomic_get(&stop)||!xd_owner_admit())break;
  int rc=xd_owner_prepare(client,control_topic,input_topic);
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
  if(!rc&&!fault_command_registered){
   rc=xm_topic_vm(fault_command_topic,sizeof(fault_command_topic),client->id.vm_id,"command");
   if(rc>=0)rc=xm_topic_vm(fault_result_topic,sizeof(fault_result_topic),client->id.vm_id,"command-result");
   if(rc>=0)rc=xm_zclient_subscribe(client,fault_command_topic,1);
   fault_command_registered=rc>=0;if(rc>0)rc=0;
  }
#endif
  if(!rc&&dual_mode&&!perception_registered){
   rc=xm_zclient_subscribe(client,perception_topic,0);
   perception_registered=!rc;
  }
  if(!rc&&binary.topic&&!binary_registered){
   client->binary=binary;rc=xm_zclient_subscribe(client,binary.topic,0);binary_registered=!rc;
  }
  uint64_t after=clock_us();
  k_mutex_lock(&exchange_lock,K_FOREVER);stats.prepare_max_us=MAX(stats.prepare_max_us,after-before);k_mutex_unlock(&exchange_lock);
  if(rc){(void)k_sem_take(&io_wake,K_MSEC(10));continue;}
  initialized=true;
  if(atomic_get(&stop)||!xd_owner_admit())break;
  /* Probe socket readiness without an idle wait. Frame reads/connect still
   * have the original bounded I/O budget. Idle below is mailbox-wakeable. */
#if defined(CONFIG_XENOVA_DRIVE_WARM)
  xd_owner_context(&working);
#endif
  xd_manual_bind(&manual_source,working.app_instances[1]);
  client->accept_input=accept_input;client->accept_context=NULL;
  before=clock_us();(void)xm_zclient_poll(client,0);after=clock_us();
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
  receive_fault_command();observe_fault_command();observe_resource_stress();
#endif
  k_mutex_lock(&exchange_lock,K_FOREVER);stats.poll_max_us=MAX(stats.poll_max_us,after-before);k_mutex_unlock(&exchange_lock);
  refresh();
  if(atomic_get(&stop)||!xd_owner_admit())break;
  send_command();send_report();
  (void)k_sem_take(&io_wake,K_MSEC(10));
 }
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
 if(fault_running){fault_running=false;drive_auto_delay_stop();fault_result(&fault_command,"FAILED","AGENT_STOPPING",(uint64_t)MAX(k_uptime_get()-fault_started_ms,0));}
 if(resource_running){if(resource_command.action==XM_CPU_STRESS)drive_cpu_stress_stop();else drive_memory_stress_stop();resource_running=false;resource_duration_ms=(uint64_t)MAX(k_uptime_get()-resource_started_ms,0);resource_stress_result(&resource_command,"FAILED","AGENT_STOPPING",resource_duration_ms);}
#endif
 if(initialized)xm_zclient_close(client);
 k_mutex_lock(&exchange_lock,K_FOREVER);
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
 if(initialized)stats.close_error=client->socket_io.close_error;
#endif
 struct xd_delivery d;
 bool discarded=command_pending;
 if(discarded){d=result(&pending,XD_STOPPED,-ESHUTDOWN);command_pending=false;stats.stopped++;}
 stats.event_dropped+=event_count;
 for(unsigned i=0;i<ARRAY_SIZE(heartbeat);i++){
  if(i<2)stats.heartbeat_dropped+=heartbeat_pending[i];
  else stats.readiness_dropped+=heartbeat_pending[i];
  heartbeat_pending[i]=false;
 }
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
int xd_io_binary(const struct xm_zbinary *in){
 if(!in||!in->topic||!in->buffer||!in->receive||!in->capacity||in->capacity>2048||strlen(in->topic)>=XM_TOPIC_MAX)return -EINVAL;
 if(k_mutex_lock(&io_lifecycle_lock,K_NO_WAIT))return -EBUSY;
 int rc=attempted_start?-EALREADY:0;if(!rc)binary=*in;
 k_mutex_unlock(&io_lifecycle_lock);return rc;
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
int xd_io_memory_read(struct xdr_app_region *region,struct xdr_app_buffer *detail){
 if(!region||!detail)return -EINVAL;
 if(k_mutex_lock(&io_lifecycle_lock,K_NO_WAIT))return -EBUSY;
 if(k_mutex_lock(&exchange_lock,K_NO_WAIT)){k_mutex_unlock(&io_lifecycle_lock);return -EBUSY;}
 if(attempted_start&&!dual_mode&&(!client||!client->id.app_id||strcmp(client->id.app_id,"manual"))){
  k_mutex_unlock(&exchange_lock);k_mutex_unlock(&io_lifecycle_lock);return -ENOTSUP;
 }
 struct xdr_app_buffer value=input_memory;
 if(!value.phase){value.input_status=XDR_INPUT_MISSING;if(!value.source_rc)value.source_rc=-ENODATA;}
 value.revoked=atomic_get(&stop)!=0;
 if(value.input_status==XDR_INPUT_ACCEPTED){
  int64_t now=k_uptime_get();
  /* Same original source age and strict 200ms boundary as manual_session.
   * Expiry changes usability, never content occupancy or receive time. */
  if(value.source_ms<0||now<value.source_ms||input_age_ms>200||
     (uint64_t)(now-value.source_ms)>200-input_age_ms)value.input_status=XDR_INPUT_EXPIRED;
 }
 if(value.phase&&(!shared.connected||value.revoked))value.input_status=XDR_INPUT_CONTEXT_CHANGED;
 *region=(struct xdr_app_region){.reserved=sizeof(shared.input),.capacity=sizeof(shared.input.payload)-1,
  .used=shared.input.length,.peak=input_peak,.contention=(uint32_t)atomic_get(&snapshot_busy)};
 *detail=value;
 k_mutex_unlock(&exchange_lock);k_mutex_unlock(&io_lifecycle_lock);return 0;
}
int xd_io_command(struct xd_candidate *c) {
 if(!c||!c->id||strnlen(c->json,sizeof(c->json))==sizeof(c->json)||strnlen(c->topic,sizeof(c->topic))==sizeof(c->topic))return -EINVAL;
 if(atomic_get(&stop))return -ESHUTDOWN;
 if(k_mutex_lock(&exchange_lock,K_NO_WAIT))return -EBUSY;
 int rc=0;struct xd_delivery old;
 bool replaced=false;
 if(atomic_get(&stop))rc=-ESHUTDOWN;
 else if(!context_ok(c->control_revision,c->connection_revision)||!app_ok(c->topic,c->app_instance))rc=-EXDEV;
 else {
  if(command_pending){old=result(&pending,XD_REPLACED,-EALREADY);stats.replaced++;replaced=true;}
  c->queued_us=clock_us();pending=*c;command_pending=true;stats.enqueued++;
 }
 k_mutex_unlock(&exchange_lock);
 if(replaced)xd_delivery_record(&old);
 if(!rc)k_sem_give(&io_wake);
 return rc;
}
int xd_io_report_at(const char *topic,const char *type,const char *json,uint64_t completed,const struct xd_inputs *context,int64_t produced_ms) {
 if(!topic||!type||!json||!context||!completed||strlen(topic)>=XM_TOPIC_MAX||strlen(type)>=32||strlen(json)>=256)return -EINVAL;
 bool readiness=!strcmp(type,"app.readiness")||!strcmp(type,"app.lifecycle");
 bool beat=!strcmp(type,"app.heartbeat")||readiness;
 if(!beat&&strcmp(type,"perception.event"))return -ENOTSUP;
 int64_t now=k_uptime_get(), lifetime=readiness?300:1000;
 if(produced_ms<0||produced_ms>now)return -EINVAL;
 if(now-produced_ms>=lifetime)return -ETIME;
 if(atomic_get(&stop))return -ESHUTDOWN;
 if(k_mutex_lock(&exchange_lock,K_NO_WAIT))return -EBUSY;
 int rc=0;
 if(atomic_get(&stop))rc=-ESHUTDOWN;
 else if(!context_ok(context->control_revision,context->connection_revision)||
   !app_ok(topic,context->app_instances[app_index(topic)==1?1:0]))rc=-EXDEV;
 else if(!beat&&event_count==ARRAY_SIZE(events)){stats.event_dropped++;rc=-ENOBUFS;}
 else {
  unsigned slot=0;
  if(beat&&dual_mode){
   /* Fixed logical producers, not an unbounded topic-indexed queue. */
   struct xm_topic_info info;xm_topic_parse(topic,&info);
   if(!strcmp(info.app_id,"autonomous"))slot=1;
   else if(strcmp(info.app_id,"manual")){k_mutex_unlock(&exchange_lock);return -EINVAL;}
   if(info.vm_id[0]&&(!client->id.vm_id||strcmp(info.vm_id,client->id.vm_id))){k_mutex_unlock(&exchange_lock);return -EINVAL;}
  }
  if(readiness)slot+=2;
  struct report *r=beat?&heartbeat[slot]:&events[(event_head+event_count++)%ARRAY_SIZE(events)];
  if(beat){
   if(readiness)stats.readiness_replaced+=heartbeat_pending[slot];
   else stats.heartbeat_replaced+=heartbeat_pending[slot];
   heartbeat_pending[slot]=true;
  }
  strcpy(r->topic,topic);strcpy(r->type,type);strcpy(r->json,json);
  strcpy(r->app_instance,context->app_instances[app_index(topic)==1?1:0]);
  r->completed=completed;r->control_revision=context->control_revision;r->connection_revision=context->connection_revision;
  r->expires_ms=produced_ms+lifetime;
 }
 k_mutex_unlock(&exchange_lock);if(!rc&&!strcmp(type,"app.lifecycle"))k_sem_give(&io_wake);return rc;
}
int xd_io_report(const char *topic,const char *type,const char *json,uint64_t completed,const struct xd_inputs *context) {
 return xd_io_report_at(topic,type,json,completed,context,k_uptime_get());
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
