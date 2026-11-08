/* Product owner/exchange with real Zephyr thread/join; bounded I/O is injected. */
#include <xenova/drive_io.h>
#include <zephyr/sys/atomic.h>
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
#include <xenova/auto_delay.h>
#endif
static const char *scenario;
static struct xm_zclient client;
static struct xd_inputs snapshot,previous;
static struct xd_candidate command;
static struct xd_delivery results[32];
static unsigned result_count,sends,beats,readies,closes;
static unsigned subscriptions,auto_beats,manual_beats;
static atomic_t block_poll,block_send,block_prepare;
static atomic_t new_sender,source_closed;
static atomic_bool connected=true;
K_SEM_DEFINE(entered,0,1);
K_SEM_DEFINE(release,0,1);
K_SEM_DEFINE(poll_entered,0,1);
static bool is(const char *s){return scenario&&!strcmp(scenario,s);}
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
/* Command/owner boundary only. Real calculation health and pool exhaustion
 * are exercised separately by workers and memory-native product tests. */
K_MUTEX_DEFINE(fault_results_lock);
static unsigned trust_events,fault_results,fault_starts;
static char fault_result[512],fault_status[12];
static int64_t injected_until;
int drive_auto_delay_start(uint32_t delay,uint32_t duration){
 assert(delay==2&&duration==1);fault_starts++;injected_until=k_uptime_get()+1000;return 0;
}
void drive_auto_delay_stop(void){injected_until=0;}
void drive_auto_delay_status_get(struct drive_auto_delay_status *out){
 *out=(struct drive_auto_delay_status){.active=k_uptime_get()<injected_until,
  .unhealthy=true,.consecutive_deadline_misses=5};
}
int __wrap_xm_zclient_publish(struct xm_zclient *c,const char *topic,const char *type,const char *json){
 ARG_UNUSED(c);ARG_UNUSED(topic);k_mutex_lock(&fault_results_lock,K_FOREVER);
 if(!strcmp(type,"vm.trust"))trust_events++;
 else {assert(!strcmp(type,"agent.command_result"));fault_results++;
  assert(strlen(json)<sizeof(fault_result));strcpy(fault_result,json);
  const char *states[]={"RUNNING","SUCCEEDED","FAILED","REJECTED"};
  for(unsigned i=0;i<ARRAY_SIZE(states);i++)if(strstr(json,states[i]))strcpy(fault_status,states[i]);
 }
 k_mutex_unlock(&fault_results_lock);return 0;
}
#endif
bool xd_owner_admit(void){return !atomic_get(&source_closed);}
k_tid_t __real_z_impl_k_thread_create(struct k_thread *,k_thread_stack_t *,size_t,k_thread_entry_t,void *,void *,void *,int,uint32_t,k_timeout_t);
k_tid_t __wrap_z_impl_k_thread_create(struct k_thread *t,k_thread_stack_t *stack,size_t size,k_thread_entry_t entry,void *a,void *b,void *c,int priority,uint32_t options,k_timeout_t delay) {
 if(is("start-fail"))return NULL;
 if(is("start-race")&&priority==CONFIG_XENOVA_DRIVE_IO_PRIORITY){k_sem_give(&entered);k_sem_take(&release,K_SECONDS(1));}
 return __real_z_impl_k_thread_create(t,stack,size,entry,a,b,c,priority,options,delay);
}
int xd_owner_prepare(struct xm_zclient *c,const char *control,const char *input) {
 assert(!strcmp(control,"control")&&!strcmp(input,"input"));
 if(atomic_get(&block_prepare)){k_sem_give(&entered);k_sem_take(&release,K_MSEC(500));}
 c->connected=connected;c->id.app_id="manual";
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
 c->id.vm_id="11111111-1111-4111-8111-111111111111";
 strcpy(c->id.boot_id,"22222222-2222-4222-8222-222222222222");
#endif
 return 0;
}
int __wrap_xm_zclient_poll(struct xm_zclient *c,int32_t ms) {
 k_sem_give(&poll_entered);
 if(atomic_get(&block_poll))k_sem_take(&release,(is("heartbeat-expire")||is("readiness-expire"))?K_SECONDS(2):K_MSEC(250));
 else k_sleep(K_MSEC(ms));
 c->connected=connected;
 return 0;
}
int __wrap_xm_zclient_latest(struct xm_zclient *c,const char *topic,struct xm_envelope *e,int64_t *age) {
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
 if(strstr(topic,"/command")){
  struct xm_zlatest *s=&c->latest[3];if(!s->len)return -ENODATA;
  *e=(struct xm_envelope){.type={"agent.command",13},.payload={s->data,s->len}};
  *age=k_uptime_get()-s->rx_ms;return 0;
 }
#endif
 unsigned i=!strcmp(topic,"control")?0:!strcmp(topic,"perception")?2:1;
 struct xm_zlatest *s=&c->latest[i];
 *e=(struct xm_envelope){.type={!i?"vehicle.control":i==2?"perception.result":"drive.input",!i?15:i==2?17:11},
  .payload={s->data,s->len},.seq=(int64_t)s->rx_ms+1,
  .source_id={"source",6},.boot_id={"11111111-1111-4111-8111-111111111111",36},
  .instance_id={"22222222-2222-4222-8222-222222222222",36}};
 if(!i&&atomic_get(&new_sender))e->instance_id=(struct xm_span){"44444444-4444-4444-8444-444444444444",36};
 *age=k_uptime_get()-s->rx_ms;return 0;
}
int __wrap_xm_zclient_subscribe(struct xm_zclient *c,const char *topic,uint8_t qos) {
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
 if(strstr(topic,"/command")){assert(qos==1);strcpy(c->latest[3].topic,topic);return 0;}
#endif
 ARG_UNUSED(c);assert(!strcmp(topic,"perception")&&qos==0);subscriptions++;return 0;
}
int __wrap_xm_zclient_publish_until(struct xm_zclient *c,const char *topic,const char *type,const char *json,int64_t end) {
 assert(end>k_uptime_get());
 char before[160];strcpy(before,json);
 if(is("wake"))k_sem_give(&entered);
 if(atomic_get(&block_send)){k_sem_give(&entered);k_sem_take(&release,K_MSEC(250));}
 assert(!strcmp(before,json)); /* In-flight bytes are never replaced. */
 if(!strcmp(type,"app.readiness")){readies++;}
 else if(!strcmp(type,"app.heartbeat")){
  beats++;
  if(!strcmp(topic,"xenova/v1/apps/autonomous/events"))auto_beats++;
  if(!strcmp(topic,"xenova/v1/apps/manual/events"))manual_beats++;
 }
 else if(!strcmp(type,"perception.event")&&is("events-starve")) {
  /* A continuously replenished external event source must not starve the
   * one heartbeat generated by main. Never synthesize another heartbeat. */
  int rc=xd_io_report(topic,type,"{}",1,&snapshot);
  assert(!rc||rc==-ESHUTDOWN);
 }
 else if(!strcmp(type,"drive.command"))sends++;
 c->message_counter++;strcpy(c->pubs[0].topic,topic);c->pubs[0].seq++;
 return is("send-fail")?-ETIMEDOUT:0;
}
void __wrap_xm_zclient_close(struct xm_zclient *c){c->connected=false;closes++;}
void xd_delivery_record(const struct xd_delivery *d){assert(result_count<ARRAY_SIZE(results));results[result_count++]=*d;}
static void update(void) {
 strcpy(client.latest[0].topic,"control");strcpy(client.latest[0].data,"{\"generation\":1}");client.latest[0].len=strlen(client.latest[0].data);
 strcpy(client.latest[1].topic,"input");strcpy(client.latest[1].data,"{\"keys\":[\"up\"]}");client.latest[1].len=strlen(client.latest[1].data);
 client.latest[0].rx_ms=client.latest[1].rx_ms=k_uptime_get();
 strcpy(client.latest[2].topic,"perception");strcpy(client.latest[2].data,"{}");client.latest[2].len=2;
}
static void read_ready(void) {
 int64_t end=k_uptime_get()+1000;
 do {k_sleep(K_MSEC(10));assert(!xd_io_snapshot(&snapshot));}while((!snapshot.connected||!snapshot.input.length)&&k_uptime_get()<end);
 assert(snapshot.connected&&snapshot.input.length);
}
static void candidate(unsigned id) {
 command=(struct xd_candidate){.id=id,.created_us=(uint64_t)k_uptime_get()*1000,
  .queued_us=(uint64_t)k_uptime_get()*1000,.control_revision=snapshot.control_revision,
  .connection_revision=snapshot.connection_revision,.expires_ms=k_uptime_get()+200};
 strcpy(command.topic,"events");snprintf(command.json,sizeof(command.json),"{\"generation\":1,\"id\":%u}",id);
}
static void freeze(void){atomic_set(&block_poll,1);k_sem_reset(&poll_entered);assert(!k_sem_take(&poll_entered,K_SECONDS(1)));}
static void unfreeze(void){atomic_set(&block_poll,0);k_sem_give(&release);}
K_THREAD_STACK_DEFINE(stop_stack,2048);
static struct k_thread stopper;
static void stop_during_create(void *a,void *b,void *c) {
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 assert(!k_sem_take(&entered,K_SECONDS(1)));xd_io_stop_request();k_sem_give(&release);
}
int main(void) {
 scenario=getenv("XL_CASE");assert(scenario);update();
 if(is("start-race"))k_thread_create(&stopper,stop_stack,K_THREAD_STACK_SIZEOF(stop_stack),stop_during_create,NULL,NULL,NULL,6,0,K_NO_WAIT);
 if(is("prepare-delay")||is("stop-prepare"))atomic_set(&block_prepare,1);
 int64_t begin=k_uptime_get();int rc=is("dual")?xd_io_start_dual(&client,"control","input","perception"):xd_io_start(&client,"control","input");
 if(is("start-fail")){assert(rc==-ENOMEM);assert(!xd_io_join(k_uptime_get()+100));assert(!closes);puts("PASS owner start failure");exit(0);}
 if(is("start-race")){
  assert(!rc);assert(!k_thread_join(&stopper,K_SECONDS(1)));assert(!xd_io_join(k_uptime_get()+1000));
  struct xd_io_stats s;assert(!xd_io_stats_get(&s));assert(s.started&&s.joined&&!closes&&!sends);puts("PASS late owner creation is owned and joined");exit(0);
 }
 assert(!rc&&k_uptime_get()-begin<20);
 if(atomic_get(&block_prepare)) {
  assert(!k_sem_take(&entered,K_SECONDS(1)));
  for(unsigned i=0;i<5;i++){assert(!xd_io_snapshot(&snapshot));k_sleep(K_MSEC(20));}
  assert(!sends&&!beats);
  if(is("stop-prepare")){xd_io_stop_request();assert(xd_io_join(k_uptime_get()+10)<0);}
  atomic_set(&block_prepare,0);k_sem_give(&release);
 } else {
  read_ready();previous=snapshot;
  if(is("fault-scope")) {
#if defined(CONFIG_XENOVA_AUTO_DELAY_INJECTION)
   freeze();struct xm_zlatest *s=&client.latest[3];assert(s->topic[0]);
   snprintf(s->data,sizeof(s->data),"{\"command_id\":\"test-1\",\"operation_id\":\"op-1\",\"expected_boot_id\":\"22222222-2222-4222-8222-222222222222\",\"expires_at\":\"2026-11-07T00:00:00Z\",\"action\":\"auto_delay\",\"parameters\":{\"delay_ms\":2,\"duration_s\":1}}");
   s->len=strlen(s->data);s->rx_ms=k_uptime_get();unfreeze();
   int64_t until=k_uptime_get()+1500;
   for(;;){
    k_mutex_lock(&fault_results_lock,K_FOREVER);bool done=fault_results>=2;k_mutex_unlock(&fault_results_lock);
    if(done||k_uptime_get()>=until)break;k_sleep(K_MSEC(10));
   }
   freeze();assert(fault_results==2&&fault_starts==1&&!trust_events);
   assert(strstr(fault_result,"test-1")&&strstr(fault_result,"op-1"));
   assert(!strcmp(fault_status,"SUCCEEDED"));
   /* Same request replays the original failure, never a second injection. */
   s->rx_ms++;unfreeze();k_sleep(K_MSEC(30));freeze();
   assert(fault_results==3&&fault_starts==1&&!trust_events);
   assert(strstr(fault_result,"test-1")&&strstr(fault_result,"op-1"));
   unfreeze();candidate(1);assert(!xd_io_command(&command));k_sleep(K_MSEC(30));assert(sends==1);
#else
   assert(false);
#endif
  } else if(is("dual")) {
   assert(snapshot.dual_mode&&!snapshot.perception.error&&snapshot.perception.length==2);
   assert(subscriptions==1);
   assert(xd_io_start(&client,"control","input")==-EINVAL);
   assert(!xd_io_start_dual(&client,"control","input","perception"));
   freeze();
   assert(!xd_io_report("xenova/v1/apps/manual/events","app.heartbeat","{}",1,&snapshot));
   assert(!xd_io_report("xenova/v1/apps/autonomous/events","app.heartbeat","{}",1,&snapshot));
   unfreeze();k_sleep(K_MSEC(100));
   assert(auto_beats==1&&manual_beats==1&&subscriptions==1);
  } else if(is("wake")) {
   k_sem_reset(&poll_entered);assert(!k_sem_take(&poll_entered,K_SECONDS(1)));
   candidate(1);int64_t before=k_uptime_get();assert(!xd_io_command(&command));
   assert(!k_sem_take(&entered,K_MSEC(5)));assert(k_uptime_get()==before);
  } else if(is("snapshot")||is("age")||is("malformed")) {
   freeze();
   if(is("malformed")){strcpy(client.latest[1].data,"{bad}");client.latest[1].len=5;client.latest[1].rx_ms++;}
   else {strcpy(client.latest[1].data,"{\"keys\":[]}");client.latest[1].len=strlen(client.latest[1].data);}
   unfreeze();k_sleep(K_MSEC(30));assert(!xd_io_snapshot(&snapshot));
   assert(!strcmp(previous.input.payload,"{\"keys\":[\"up\"]}"));
   assert(snapshot.input.received_ms==client.latest[1].rx_ms);
   if(is("age"))assert(k_uptime_get()-snapshot.input.received_ms>=20);
   if(is("malformed"))assert(!strcmp(snapshot.input.payload,"{bad}"));
  } else if(is("reconnect-input-first")) {
   freeze();connected=false;unfreeze();k_sleep(K_MSEC(30));
   assert(!xd_io_snapshot(&snapshot)&&!snapshot.connected);
   freeze();connected=true;client.latest[1].rx_ms=k_uptime_get();
   strcpy(client.latest[1].data,"{\"keys\":[]}");client.latest[1].len=strlen(client.latest[1].data);
   unfreeze();read_ready();
   assert(snapshot.connection_revision>=previous.connection_revision+2);
   assert(snapshot.control.seq==previous.control.seq);
   assert(snapshot.control.received_ms==previous.control.received_ms);
   assert(snapshot.input.received_ms>previous.input.received_ms);
   candidate(1);command.connection_revision=previous.connection_revision;
   assert(xd_io_command(&command)==-EXDEV); /* old candidate cannot cross reconnect */
   candidate(2);assert(!xd_io_command(&command));k_sleep(K_MSEC(60));
  } else if(is("inflight")||is("stop-send")||is("join-timeout")) {
   atomic_set(&block_send,1);candidate(1);assert(!xd_io_command(&command));
   assert(!k_sem_take(&entered,K_SECONDS(1)));
   if(is("inflight")){candidate(2);assert(!xd_io_command(&command));candidate(3);assert(!xd_io_command(&command));}
   else {xd_io_stop_request();if(is("join-timeout"))assert(xd_io_join(k_uptime_get()+10)<0);}
   atomic_set(&block_send,0);k_sem_give(&release);k_sleep(K_MSEC(60));
  } else {
   freeze();candidate(1);
   if(is("expire"))command.expires_ms=k_uptime_get()+5;
   assert(!xd_io_command(&command));
   if(is("replace")){candidate(2);assert(!xd_io_command(&command));candidate(3);assert(!xd_io_command(&command));}
   if(is("context")){strcpy(client.latest[0].data,"{\"generation\":2}");client.latest[0].len=strlen(client.latest[0].data);client.latest[0].rx_ms++;}
   if(is("same-control"))client.latest[0].rx_ms++;
   if(is("identity"))atomic_set(&new_sender,1);
   if(is("stop-guard"))atomic_set(&source_closed,1);
   if(is("disconnect"))connected=false;
   if(is("heartbeat")){assert(xd_io_report("events","app.heartbeat","{}",0,&snapshot)==-EINVAL);assert(!xd_io_report("events","app.heartbeat","{\"uptime_s\":1}",1,&snapshot));}
   if(is("events-full")){
    for(unsigned i=0;i<4;i++)assert(!xd_io_report("events","perception.event","{}",1,&snapshot));
    assert(xd_io_report("events","perception.event","{}",1,&snapshot)==-ENOBUFS);
   }
   if(is("events-starve")) {
    for(unsigned i=0;i<4;i++)assert(!xd_io_report("events","perception.event","{}",1,&snapshot));
    assert(!xd_io_report("events","app.heartbeat","{\"uptime_s\":1}",1,&snapshot));
   }
   if(is("heartbeat-expire")) {
    assert(!xd_io_report("events","app.heartbeat","{\"uptime_s\":1}",1,&snapshot));
    k_sleep(K_MSEC(1100));
   }
   if(is("readiness")||is("readiness-expire")){
    assert(!xd_io_report("events","app.readiness","{\"progress_seq\":\"1\"}",1,&snapshot));
    assert(!xd_io_report("events","app.readiness","{\"progress_seq\":\"2\"}",2,&snapshot));
    if(is("readiness-expire"))k_sleep(K_MSEC(350));
   }
   if(is("producer-readiness")||is("producer-heartbeat")){
    bool ready=is("producer-readiness");int64_t lifetime=ready?300:1000;
    const char *type=ready?"app.readiness":"app.heartbeat";
    /* Receipt by the coordinator must not renew the actual producer's age. */
    k_sleep(K_MSEC(lifetime+1));int64_t now=k_uptime_get();
    assert(xd_io_report_at("events",type,"{}",1,&snapshot,now+1)==-EINVAL);
    assert(xd_io_report_at("events",type,"{}",1,&snapshot,now-lifetime)==-ETIME);
    assert(!xd_io_report_at("events",type,"{}",1,&snapshot,now-lifetime+50));
    k_sleep(K_MSEC(70));
   }
   if(is("expire"))k_sleep(K_MSEC(20));
   unfreeze();k_sleep(K_MSEC(120));
   if(is("heartbeat")){assert(beats==1);k_sleep(K_MSEC(100));assert(beats==1);}
   if(is("events-starve")){assert(beats==1);k_sleep(K_MSEC(100));assert(beats==1);}
  }
 }
 assert(!xd_io_join(k_uptime_get()+1000));
 struct xd_io_stats stats;assert(!xd_io_stats_get(&stats));assert(stats.started&&stats.joined&&closes==1);
 if(is("replace"))assert(stats.enqueued==3&&stats.replaced==2&&sends==1);
 if(is("inflight"))assert(stats.enqueued==3&&stats.replaced==1&&sends==2);
 if(is("context")||is("disconnect")||is("identity"))assert(!sends&&stats.context_dropped==1);
 if(is("same-control"))assert(sends==1&&!stats.context_dropped);
 if(is("stop-guard"))assert(!sends&&stats.stopped==1);
 if(is("expire"))assert(!sends&&stats.expired==1);
 if(is("send-fail"))assert(stats.failed==1&&stats.sent==0);
 if(is("events-full"))assert(stats.event_dropped>=1);
 if(is("heartbeat-expire"))assert(!beats&&!stats.event_dropped&&stats.heartbeat_dropped==1&&stats.heartbeat_expired==1);
 if(is("readiness"))assert(readies==1&&stats.readiness_replaced==1&&!stats.heartbeat_replaced);
 if(is("readiness-expire"))assert(!readies&&stats.readiness_dropped==1&&stats.readiness_expired==1&&!stats.heartbeat_dropped&&!stats.event_dropped);
 if(is("producer-readiness"))assert(!readies&&stats.readiness_expired==1&&!stats.event_dropped);
 if(is("producer-heartbeat"))assert(!beats&&stats.heartbeat_expired==1&&!stats.event_dropped);
 if(is("reconnect-input-first"))assert(sends==1);
 candidate(9);assert(xd_io_command(&command)==-ESHUTDOWN);
 assert(xd_io_start(&client,"control","input")==-ESHUTDOWN);
 printf("PASS real owner/exchange %s enqueued=%llu replaced=%llu sent=%llu failed=%llu records=%u\n",scenario,
  (unsigned long long)stats.enqueued,(unsigned long long)stats.replaced,(unsigned long long)stats.sent,(unsigned long long)stats.failed,result_count);
 exit(0);
}
