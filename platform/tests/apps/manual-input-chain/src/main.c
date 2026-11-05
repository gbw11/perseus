/* SPDX-License-Identifier: Apache-2.0 */
/* Product input codec/receive/slot -> actual job owner -> default MANUAL
 * adapter/manual_step -> retained result/read metadata. MQTT latest delivery
 * alone is a fixture; Diagnostics clock/cache and worker threads are real.
 * Nonwarm stateless callback acceptance, not lattice vehicle/clock readiness,
 * authority, command publication or physical output acceptance. */
#include <zephyr/ztest.h>
#include "workers.h"
#include "io.c"

#define VM "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
#define SESSION "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
#define INSTANCE "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
static struct xm_zclient transport;
static struct xd_inputs snapshot;
static struct drive_job job;
static struct drive_completion completion;
static char encoded[1280];
static struct xdr_app_region input_region,owner_regions[3];
static struct xdr_app_buffer input_detail,owner_details[3];
static struct drive_adapter automatic,manual;
static struct auto_cfg auto_config;
static struct manual_cfg manual_config;

/* Same external latest-cache boundary as the original transport. The core
 * decoder still validates actual encoded fixture bytes on every read. */
int xm_zclient_latest(struct xm_zclient *c,const char *topic,struct xm_envelope *env,int64_t *age){
 for(unsigned i=0;i<ARRAY_SIZE(c->latest);i++){
  struct xm_zlatest *slot=&c->latest[i];
  if(!strcmp(slot->topic,topic)&&slot->len){
   const char *code;int rc=xm_envelope_decode(topic,slot->data,slot->len,env,&code);
   if(rc)return rc;*age=k_uptime_get()-slot->rx_ms;return 0;
  }
 }
 return -ENOENT;
}
static void incoming(const char *topic,const char *type,const char *payload,unsigned slot){
 struct xm_identity identity;
 zassert_ok(xm_identity_init(&identity,NULL,"input-fixture",slot?"manual":NULL,VM,INSTANCE));
 int length=xm_envelope_encode(encoded,sizeof(encoded),&identity,topic,type,1,"fixture-1",
  "2026-11-04T00:00:00.000Z",payload);zassert_true(length>0);
 struct xm_envelope env;const char *code;zassert_ok(xm_envelope_decode(topic,encoded,length,&env,&code));
 int64_t received=k_uptime_get();zassert_ok(accept_input(NULL,topic,&env,received));
 struct xm_zlatest *latest=&transport.latest[slot];strcpy(latest->topic,topic);
 memcpy(latest->data,encoded,length);latest->len=length;latest->rx_ms=received;
}
static void wait_completed(void){
 int64_t end=k_uptime_get()+500;struct drive_task_status status;
 for(;;){
  int rc=drive_workers_status(DRIVE_TASK_MANUAL,&status);
  if(!rc&&status.completed_sequence==job.sequence)return;
  zassert_true(!rc||rc==-EBUSY);zassert_true(k_uptime_get()<end);k_sleep(K_MSEC(1));
 }
}
static void owner_memory(void){
 int64_t end=k_uptime_get()+100;int rc;
 do{rc=drive_workers_memory_read(owner_regions,owner_details);if(!rc)return;
  zassert_equal(rc,-EBUSY);zassert_true(k_uptime_get()<end);k_sleep(K_MSEC(1));
 }while(true);
}
ZTEST(manual_chain,test_actual_input_snapshot_job_calculation_retention_and_read){
 memset(&transport,0,sizeof(transport));transport.connected=true;transport.id.vm_id=VM;transport.id.app_id="manual";
 client=&transport;attempted_start=true;dual_mode=false;
 strcpy(control_topic,"xenova/v1/vehicle/control");strcpy(input_topic,"xenova/v1/apps/manual/input");
 const char *control="{\"generation\":1,\"mode\":\"MANUAL\",\"active_app\":\"manual\",\"active_vm_id\":\"" VM
  "\",\"manual_policy\":\"CONTINUE_SESSION\",\"manual_session\":\"" SESSION "\"}";
 const char *input="{\"profile\":\"manual-session-1\",\"session_id\":\"" SESSION "\",\"sample_seq\":\"1\","
  "\"action_seq\":\"1\",\"generation\":1,\"valid\":true,\"age_ms\":0,\"keys\":[\"up\"]}";
 incoming(control_topic,"vehicle.control",control,0);incoming(input_topic,"drive.input",input,1);
 refresh();zassert_ok(xd_io_snapshot(&snapshot));zassert_ok(xd_io_memory_read(&input_region,&input_detail));
 zassert_equal(input_detail.input_status,XDR_INPUT_ACCEPTED);zassert_equal(input_region.used,strlen(input));
 zassert_equal(snapshot.input.length,input_region.used);zassert_equal(input_detail.sequence,snapshot.input.seq);
 zassert_equal(input_detail.source_ms,snapshot.input.received_ms);
 drive_auto_adapter(&automatic,&auto_config);drive_manual_adapter(&manual,&manual_config);
 zassert_ok(drive_workers_start(&automatic,&manual));owner_memory();
 for(unsigned i=0;i<3;i++)zassert_equal(owner_regions[i].used,0);
 struct drive_task_status status;zassert_ok(drive_workers_status(DRIVE_TASK_MANUAL,&status));
 uint64_t now;zassert_ok(xdiag_clock_us(&now));
 job=(struct drive_job){.sequence=1,.lifetime=status.lifetime,.context_revision=snapshot.control_revision,
  .connection_revision=snapshot.connection_revision,.release_us=now,.deadline_us=now+20000,.iteration_begin_us=now,
  .input_received_ms=snapshot.input.received_ms,.input_seq=snapshot.input.seq,.control_seq=snapshot.control.seq,
  .control_length=snapshot.control.length,.input_length=snapshot.input.length,.manual_witness=snapshot.manual_witness};
 strcpy(job.vm_id,snapshot.vm_id);strcpy(job.app_instance,snapshot.app_instances[1]);
 memcpy(job.control,snapshot.control.payload,job.control_length);memcpy(job.input,snapshot.input.payload,job.input_length);
 zassert_ok(drive_workers_offer(DRIVE_TASK_MANUAL,&job));wait_completed();owner_memory();
 zassert_equal(owner_details[2].phase,XDR_BUFFER_RETAINED);zassert_equal(owner_details[2].sequence,job.sequence);
 zassert_true(owner_details[2].performed);zassert_equal(owner_details[2].source_rc,0);
 zassert_equal(owner_details[2].read_sequence,0);zassert_true(owner_regions[2].used>0);
 zassert_equal(owner_regions[0].peak,sizeof(job));zassert_equal(owner_regions[1].peak,sizeof(job));
 zassert_equal(owner_details[2].source_ms,input_detail.source_ms);
 owner_memory();zassert_equal(owner_details[2].read_sequence,0); /* diagnostic reads never consume a result */
 zassert_ok(drive_workers_read(DRIVE_TASK_MANUAL,snapshot.control_revision,&completion));
 zassert_true(completion.performed);zassert_ok(completion.rc);zassert_equal(completion.progress,1);
 zassert_true(completion.sample.v_mps>0);zassert_false(completion.health.restrict_function);
 zassert_equal(completion.source.input_seq,snapshot.input.seq);
 zassert_equal(completion.source.input_received_ms,snapshot.input.received_ms);
 zassert_mem_equal(completion.source.input,snapshot.input.payload,snapshot.input.length);
 owner_memory();zassert_equal(owner_details[2].read_sequence,completion.sequence);
 zassert_equal(owner_details[2].phase,XDR_BUFFER_RETAINED);zassert_true(owner_regions[2].used>0);
 printk("MANUAL_CHAIN source_seq=%lld source_ms=%lld job=%llu lifetime=%llu progress=%llu v_milli=%d retained=%llu read=%llu\n",
  (long long)snapshot.input.seq,(long long)snapshot.input.received_ms,(unsigned long long)completion.sequence,
  (unsigned long long)completion.source.lifetime,(unsigned long long)completion.progress,
  (int)(completion.sample.v_mps*1000),(unsigned long long)owner_regions[2].used,
  (unsigned long long)owner_details[2].read_sequence);
 zassert_ok(drive_workers_join(k_uptime_get()+500));xd_io_stop_request();
 zassert_ok(xd_io_memory_read(&input_region,&input_detail));zassert_true(input_detail.revoked);
 zassert_equal(input_detail.input_status,XDR_INPUT_CONTEXT_CHANGED);zassert_true(input_region.used>0);
}
ZTEST_SUITE(manual_chain,NULL,NULL,NULL,NULL,NULL);
