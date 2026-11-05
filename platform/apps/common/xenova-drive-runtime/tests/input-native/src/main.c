/* SPDX-License-Identifier: Apache-2.0 */
/* Product accept_input -> refresh -> public metadata reader, real core parsers
 * and Zephyr mutexes. Only external MQTT latest delivery/clock/runtime binding
 * are fixtures. No broker, board, command execution or copied reader logic. */
#include <zephyr/ztest.h>
#include <xenova/mqtt/xm.h>
#include <string.h>
static int64_t test_ms;
static unsigned control_parses,input_parses;
static int64_t input_test_uptime_get(void){return test_ms;}
static int counted_control_parse(struct xm_span p,struct xm_vehicle_control *out){
 control_parses++;return xm_vehicle_control_parse(p,out);
}
static int counted_input_parse(struct xm_span p,struct xm_manual_sample *out){
 input_parses++;return xm_manual_sample_parse(p,out);
}
#define k_uptime_get input_test_uptime_get
#define xm_vehicle_control_parse counted_control_parse
#define xm_manual_sample_parse counted_input_parse
#include "../../../src/io.c"
#undef k_uptime_get
#undef xm_vehicle_control_parse
#undef xm_manual_sample_parse

static const char *VM="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
static const char *SESSION="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
static const char *APP="cccccccc-cccc-4ccc-8ccc-cccccccccccc";
static const char *NEW_APP="dddddddd-dddd-4ddd-8ddd-dddddddddddd";
static struct xm_zclient transport;
static char manual_instance[XM_UUID_LEN+1];
static char wire[1025],control_wire[512];
static int latest_error;
static int64_t latest_seq[4];
static const char *latest_type[4];
static struct xdr_app_region region;
static struct xdr_app_buffer detail;

int xdiag_clock_us(uint64_t *out){*out=test_ms<0?0:(uint64_t)test_ms*1000;return 0;}
bool xd_owner_admit(void){return true;}
int xd_owner_prepare(struct xm_zclient *c,const char *a,const char *b){return 0;}
void xd_owner_context(struct xd_inputs *out){strcpy(out->vm_id,VM);strcpy(out->app_instances[1],manual_instance);}
bool xd_owner_instance(int app,const char *instance){return !strcmp(instance,manual_instance);}
void xd_delivery_record(const struct xd_delivery *in){}
int xm_zclient_subscribe(struct xm_zclient *c,const char *t,uint8_t qos){return 0;}
int xm_zclient_poll(struct xm_zclient *c,int32_t timeout){return 0;}
void xm_zclient_close(struct xm_zclient *c){c->connected=false;}
int xm_zclient_publish_as_until(struct xm_zclient *c,const char *t,const char *type,
 const char *payload,const char *instance,int64_t end){return 0;}
int xm_zclient_latest(struct xm_zclient *c,const char *topic,struct xm_envelope *out,int64_t *age){
 if(!strcmp(topic,input_topic)&&latest_error)return latest_error;
 for(unsigned i=0;i<ARRAY_SIZE(c->latest);i++){
  struct xm_zlatest *slot=&c->latest[i];
  if(!strcmp(slot->topic,topic)&&slot->len){
   *out=(struct xm_envelope){.payload={slot->data,slot->len},.seq=latest_seq[i],
    .type={latest_type[i],strlen(latest_type[i])},.source_id={"fixture-source",14},
    .boot_id={VM,36},.instance_id={APP,36}};
   *age=test_ms-slot->rx_ms;return 0;
  }
 }
 return -ENOENT;
}
static int deliver(const char *topic,const char *type,const char *body,int64_t seq){
 struct xm_envelope env={.type={type,strlen(type)},.payload={body,strlen(body)},.seq=seq};
 int rc=accept_input(NULL,topic,&env,test_ms);if(rc)return rc;
 unsigned index=!strcmp(topic,control_topic)?0:1;
 struct xm_zlatest *slot=&transport.latest[index];
 strcpy(slot->topic,topic);strcpy(slot->data,body);slot->len=strlen(body);slot->rx_ms=test_ms;
 latest_type[index]=type;latest_seq[index]=seq;return 0;
}
static void control_value(const char *mode,unsigned generation){
 snprintf(control_wire,sizeof(control_wire),"{\"generation\":%u,\"mode\":\"%s\",\"active_app\":\"manual\","
  "\"active_vm_id\":\"%s\",\"manual_policy\":\"CONTINUE_SESSION\",\"manual_session\":\"%s\"}",generation,mode,VM,SESSION);
 zassert_ok(deliver(control_topic,"vehicle.control",control_wire,generation));
}
static void sample_value(unsigned sample,bool valid,unsigned age,unsigned generation,int64_t envelope){
 snprintf(wire,sizeof(wire),"{\"profile\":\"manual-session-1\",\"session_id\":\"%s\",\"sample_seq\":\"%u\","
  "\"action_seq\":\"%u\",\"generation\":%u,\"valid\":%s,\"age_ms\":%u,\"keys\":[\"up\"]}",
  SESSION,sample,sample,generation,valid?"true":"false",age);
 zassert_ok(deliver(input_topic,"drive.input",wire,envelope));
}
static void committed(void){refresh();zassert_ok(xd_io_memory_read(&region,&detail));}
static void reset(void *unused){
 memset(&transport,0,sizeof(transport));transport.id.app_id="drive-controller";transport.connected=true;client=&transport;
 strcpy(control_topic,"xenova/v1/vehicle/control");strcpy(input_topic,"xenova/v1/apps/manual/input");
 strcpy(perception_topic,"xenova/v1/apps/perception/events");strcpy(manual_instance,APP);
 memset(&shared,0,sizeof(shared));memset(&working,0,sizeof(working));memset(&manual_source,0,sizeof(manual_source));
 memset(&memory_source,0,sizeof(memory_source));memset(&memory_control,0,sizeof(memory_control));memory_control_rc=-ENODATA;
 memset(&input_memory,0,sizeof(input_memory));input_age_ms=0;input_peak=0;
 memset(latest_type,0,sizeof(latest_type));memset(latest_seq,0,sizeof(latest_seq));
 memset(&region,0,sizeof(region));memset(&detail,0,sizeof(detail));latest_error=0;
 atomic_clear(&stop);atomic_clear(&snapshot_busy);dual_mode=true;attempted_start=true;
 test_ms=1000;control_parses=input_parses=0;xd_manual_bind(&manual_source,manual_instance);
}
ZTEST(input_memory,test_prestart_empty_and_auto_only_unsupported){
 attempted_start=false;zassert_ok(xd_io_memory_read(&region,&detail));
 zassert_equal(region.used,0);zassert_equal(region.peak,0);zassert_equal(region.capacity,1024);
 zassert_equal(region.reserved,sizeof(shared.input));zassert_equal(detail.phase,XDR_BUFFER_EMPTY);
 zassert_equal(detail.source_rc,-ENODATA);zassert_equal(detail.input_status,XDR_INPUT_MISSING);
 attempted_start=true;dual_mode=false;transport.id.app_id="autonomous";
 region.used=77;detail.sequence=99;zassert_equal(xd_io_memory_read(&region,&detail),-ENOTSUP);
 zassert_equal(region.used,77);zassert_equal(detail.sequence,99);
 zassert_equal(xd_io_memory_read(NULL,&detail),-EINVAL);
}
ZTEST(input_memory,test_real_accept_copy_expiry_no_getter_reparse){
 control_value("MANUAL",1);sample_value(1,true,20,1,0);committed();
 zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);zassert_equal(detail.sequence,0);
 zassert_equal(detail.source_ms,1000);zassert_equal(detail.phase,XDR_BUFFER_STORED);
 zassert_equal(detail.lifetime,0);zassert_equal(detail.read_sequence,0);
 zassert_equal(region.used,strlen(wire));zassert_equal(region.peak,region.used);
 zassert_equal(control_parses,1);zassert_equal(input_parses,1);
 uint64_t used=region.used,peak=region.peak;test_ms=1180;
 zassert_ok(xd_io_memory_read(&region,&detail));zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
 test_ms++;zassert_ok(xd_io_memory_read(&region,&detail));zassert_equal(detail.input_status,XDR_INPUT_EXPIRED);
 zassert_equal(region.used,used);zassert_equal(region.peak,peak);zassert_equal(detail.source_ms,1000);
 zassert_equal(control_parses,1);zassert_equal(input_parses,1);
}
ZTEST(input_memory,test_invalid_retained_then_fresh_generation_recovers){
 control_value("MANUAL",1);sample_value(1,false,0,1,7);committed();
 zassert_equal(detail.input_status,XDR_INPUT_INVALID);zassert_true(region.used>0);
 uint64_t peak=region.peak;test_ms=1010;
 control_value("MANUAL",2);sample_value(2,true,0,2,8);committed();
 zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);zassert_equal(detail.sequence,8);
 latest_error=-EIO;committed();zassert_equal(detail.source_rc,-EIO);
 zassert_equal(detail.phase,XDR_BUFFER_EMPTY);zassert_equal(region.used,0);zassert_true(region.peak>=peak);
 latest_error=0;sample_value(3,true,0,2,9);committed();zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
}
ZTEST(input_memory,test_rejected_duplicate_never_renews_original){
 control_value("MANUAL",1);sample_value(2,true,0,1,8);committed();uint64_t used=region.used;
 test_ms=1100;zassert_equal(deliver(input_topic,"drive.input",wire,9),-EALREADY);committed();
 zassert_equal(detail.sequence,8);zassert_equal(detail.source_ms,1000);zassert_equal(region.used,used);
 zassert_equal(deliver(input_topic,"drive.input","{\"keys\":false}",10),-EBADMSG);committed();
 zassert_equal(detail.sequence,8);zassert_equal(detail.source_ms,1000);
}
ZTEST(input_memory,test_same_generation_mode_observation_keeps_original_gate){
 control_value("MANUAL",1);sample_value(1,true,0,1,1);committed();
 zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
 control_value("AUTO_READY",1);committed();zassert_equal(detail.input_status,XDR_INPUT_CONTEXT_CHANGED);
 zassert_equal(strcmp(manual_source.control.mode,"MANUAL"),0); /* existing gate unchanged */
 zassert_equal(strcmp(memory_control.mode,"AUTO_READY"),0);zassert_equal(control_parses,2);
 control_value("MANUAL",1);committed();zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
 zassert_equal(control_parses,3);zassert_equal(input_parses,1);
}
ZTEST(input_memory,test_app_binding_connection_stop_revoke_without_zeroing_content){
 control_value("MANUAL",1);sample_value(1,true,0,1,1);committed();uint64_t used=region.used;
 strcpy(manual_instance,NEW_APP);xd_manual_bind(&manual_source,manual_instance);committed();
 zassert_equal(detail.input_status,XDR_INPUT_CONTEXT_CHANGED);zassert_equal(region.used,used);
 sample_value(2,true,0,1,2);committed();zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
 uint64_t original_context=detail.context_revision,original_connection=detail.connection_revision;
 int64_t original_time=detail.source_ms;
 transport.connected=false;committed();zassert_equal(detail.input_status,XDR_INPUT_CONTEXT_CHANGED);
 zassert_equal(detail.connection_revision,original_connection);zassert_equal(detail.context_revision,original_context);
 transport.connected=true;committed();zassert_equal(detail.input_status,XDR_INPUT_CONTEXT_CHANGED);
 zassert_equal(detail.connection_revision,original_connection);zassert_equal(detail.source_ms,original_time);
 sample_value(3,true,0,1,3);committed();zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
 atomic_set(&stop,1);zassert_ok(xd_io_memory_read(&region,&detail));
 zassert_true(detail.revoked);zassert_equal(detail.input_status,XDR_INPUT_CONTEXT_CHANGED);zassert_true(region.used>0);
}
ZTEST(input_memory,test_generation_session_and_target_changes_preserve_stored_bytes){
 control_value("MANUAL",1);sample_value(1,true,0,1,1);committed();uint64_t used=region.used;
 uint64_t original_context=detail.context_revision,original_connection=detail.connection_revision;
 control_value("MANUAL",2);committed();zassert_equal(detail.input_status,XDR_INPUT_CONTEXT_CHANGED);
 zassert_equal(detail.context_revision,original_context);zassert_equal(detail.connection_revision,original_connection);
 zassert_equal(detail.source_ms,1000);zassert_equal(region.used,used);sample_value(2,true,0,2,2);committed();
 zassert_true(detail.context_revision>original_context);
 zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
 char *session=strstr(control_wire,SESSION);zassert_not_null(session);session[0]='e';
 zassert_ok(deliver(control_topic,"vehicle.control",control_wire,3));committed();
 zassert_equal(detail.input_status,XDR_INPUT_CONTEXT_CHANGED);zassert_true(region.used>0);
 control_value("MANUAL",3);sample_value(3,true,0,3,3);committed();
 zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
 char *target=strstr(control_wire,VM);zassert_not_null(target);target[0]='e';
 zassert_ok(deliver(control_topic,"vehicle.control",control_wire,4));committed();
 zassert_equal(detail.input_status,XDR_INPUT_CONTEXT_CHANGED);zassert_true(region.used>0);
 control_value("MANUAL",4);sample_value(4,true,0,4,4);committed();
 zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
}
ZTEST(input_memory,test_legacy_and_original_negative_time_are_not_fabricated){
 zassert_ok(deliver(input_topic,"drive.input","{\"keys\":[\"up\"]}",0));committed();
 zassert_equal(detail.input_status,XDR_INPUT_UNCLASSIFIED);zassert_true(region.used>0);
 control_value("MANUAL",1);sample_value(1,true,0,1,1);committed();
 zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
 zassert_ok(deliver(control_topic,"vehicle.control","{\"generation\":1,\"mode\":\"MANUAL\",\"active_app\":\"manual\"}",2));
 zassert_ok(deliver(input_topic,"drive.input","{\"keys\":[\"up\"]}",2));committed();
 zassert_equal(detail.input_status,XDR_INPUT_UNCLASSIFIED); /* same tick, old parsed strict source remains */
 transport.latest[1].rx_ms=-5;committed();zassert_equal(detail.source_ms,-5);
 zassert_equal(detail.input_status,XDR_INPUT_UNCLASSIFIED);
}
ZTEST(input_memory,test_capacity_and_peak_follow_committed_copy_not_reader){
 strcpy(wire,"{\"keys\":[\"up\"],\"padding\":\"");size_t prefix=strlen(wire);
 memset(wire+prefix,'x',1024-prefix-2);wire[1022]='"';wire[1023]='}';wire[1024]=0;
 zassert_ok(deliver(input_topic,"drive.input",wire,0));
 zassert_ok(xd_io_memory_read(&region,&detail));zassert_equal(region.used,0);zassert_equal(region.peak,0);
 committed();zassert_equal(region.used,1024);zassert_equal(region.peak,1024);
 zassert_equal(detail.content_bytes,1024);zassert_equal(detail.input_status,XDR_INPUT_UNCLASSIFIED);
 zassert_ok(deliver(input_topic,"drive.input","{}",1));committed();
 zassert_equal(region.used,2);zassert_equal(region.peak,1024);zassert_equal(region.exhausted,0);
 unsigned parsed=input_parses;zassert_ok(xd_io_memory_read(&region,&detail));
 zassert_equal(input_parses,parsed);zassert_equal(region.peak,1024);
}
K_SEM_DEFINE(held,0,1);K_SEM_DEFINE(release_lock,0,1);
K_THREAD_STACK_DEFINE(lock_stack,2048);static struct k_thread lock_thread;
static void hold_exchange(void *a,void *b,void *c){
 k_mutex_lock(&exchange_lock,K_FOREVER);k_sem_give(&held);k_sem_take(&release_lock,K_FOREVER);k_mutex_unlock(&exchange_lock);
}
ZTEST(input_memory,test_busy_reader_preserves_outputs_and_counts_only_product_contention){
 control_value("MANUAL",1);sample_value(1,true,0,1,1);committed();
 k_thread_create(&lock_thread,lock_stack,K_THREAD_STACK_SIZEOF(lock_stack),hold_exchange,NULL,NULL,NULL,-1,0,K_NO_WAIT);
 zassert_ok(k_sem_take(&held,K_SECONDS(1)));region.used=77;detail.sequence=99;
 zassert_equal(xd_io_memory_read(&region,&detail),-EBUSY);zassert_equal(region.used,77);zassert_equal(detail.sequence,99);
 zassert_equal(atomic_get(&snapshot_busy),0);struct xd_inputs snapshot;
 zassert_equal(xd_io_snapshot(&snapshot),-EBUSY);zassert_equal(atomic_get(&snapshot_busy),1);
 k_sem_give(&release_lock);zassert_ok(k_thread_join(&lock_thread,K_SECONDS(1)));
 zassert_ok(xd_io_memory_read(&region,&detail));zassert_equal(region.contention,1);
 zassert_equal(detail.input_status,XDR_INPUT_ACCEPTED);
}
ZTEST_SUITE(input_memory,NULL,NULL,reset,NULL,NULL);
