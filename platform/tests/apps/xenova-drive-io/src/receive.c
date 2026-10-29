/* Actual MQTT event/decoder/sequence/cache + io owner thread + C adapter.
 * Only peer payload delivery, socket polling and Bootstrap are substituted. */
#include <xenova/drive_io.h>
#include "adapter.h"
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define A "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
#define B "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
static struct xm_zclient client;
static struct xm_identity sender;
static bool prepared;
static const char *incoming;
static size_t incoming_length;
struct frame {const char *topic,*type,*payload;unsigned seq;};
static struct frame frames[3];
static unsigned count;
static char encoded[2048];
static struct xd_inputs snapshot;
K_SEM_DEFINE(inject,0,1);
K_SEM_DEFINE(injected,0,1);
static struct xm_span span(const char *p){return (struct xm_span){p,strlen(p)};}
bool xd_owner_admit(void){return true;}
int xd_owner_prepare(struct xm_zclient *c,const char *control,const char *input)
{
 if(!prepared){
  struct xm_zconfig cfg={.host="127.0.0.1",.port=1883,.boot_id=A,.instance_id=B};
  assert(!xm_zclient_init(c,A,NULL,"drive-controller",&cfg));
  assert(!xm_zclient_subscribe(c,control,1));assert(!xm_zclient_subscribe(c,input,0));
  c->connected=true;prepared=true;
 }
 return 0;
}
int __wrap_mqtt_subscribe(struct mqtt_client *c,const struct mqtt_subscription_list *list)
{ARG_UNUSED(c);ARG_UNUSED(list);return 0;}
int __wrap_mqtt_readall_publish_payload(struct mqtt_client *c,uint8_t *out,size_t n)
{ARG_UNUSED(c);assert(n==incoming_length);memcpy(out,incoming,n);return 0;}
int __wrap_xm_zclient_poll(struct xm_zclient *c,int32_t timeout)
{
 ARG_UNUSED(timeout);
 if(k_sem_take(&inject,K_NO_WAIT))return 0;
 for(unsigned i=0;i<count;i++){
  int n=xm_envelope_encode(encoded,sizeof(encoded),&sender,frames[i].topic,frames[i].type,
   frames[i].seq,"1","1970-01-01T00:00:00.000Z",frames[i].payload);assert(n>0);
  incoming=encoded;incoming_length=n;
  struct mqtt_evt event={.type=MQTT_EVT_PUBLISH};
  event.param.publish.message.topic.topic.utf8=(const uint8_t *)frames[i].topic;
  event.param.publish.message.topic.topic.size=strlen(frames[i].topic);
  event.param.publish.message.payload.len=n;
  c->mqtt.evt_cb(&c->mqtt,&event);
 }
 k_sem_give(&injected);return 0;
}
void __wrap_xm_zclient_close(struct xm_zclient *c){c->connected=false;}
void xd_delivery_record(const struct xd_delivery *d){ARG_UNUSED(d);}
static void send_frames(unsigned n)
{
 count=n;k_sem_give(&inject);assert(!k_sem_take(&injected,K_SECONDS(1)));
 k_sleep(K_MSEC(30));assert(!xd_io_snapshot(&snapshot));
}
int main(void)
{
 const char *control="{\"generation\":42,\"mode\":\"MANUAL\",\"active_app\":\"manual\",\"active_vm_id\":\"" A
  "\",\"manual_policy\":\"NEUTRAL_THEN_ACTION\",\"manual_session\":\"" B "\"}";
 const char *neutral="{\"profile\":\"manual-session-1\",\"session_id\":\"" B "\",\"sample_seq\":\"1\",\"action_seq\":\"1\","
  "\"generation\":42,\"keys\":[],\"valid\":true,\"age_ms\":0}";
 const char *action="{\"profile\":\"manual-session-1\",\"session_id\":\"" B "\",\"sample_seq\":\"2\",\"action_seq\":\"2\","
  "\"generation\":42,\"keys\":[\"up\"],\"valid\":true,\"age_ms\":0}";
 assert(!xm_identity_init(&sender,NULL,"browser","manual-input",A,B));
 assert(!xd_io_start_dual(&client,"xenova/v1/vehicle/control","xenova/v1/apps/manual/input","xenova/v1/apps/perception/events"));
 frames[0]=(struct frame){"xenova/v1/vehicle/control","vehicle.control",control,1};
 send_frames(1);assert(!snapshot.control.error);
 frames[0]=(struct frame){"xenova/v1/apps/manual/input","drive.input",neutral,1};
 frames[1]=(struct frame){"xenova/v1/apps/manual/input","drive.input",action,2};
 send_frames(2);assert(!snapshot.input.error&&snapshot.input.seq==2&&snapshot.manual_witness.present);
 struct drive_adapter adapter;struct manual_cfg cfg;struct drive_sample output;
 drive_manual_adapter(&adapter,&cfg);
 assert(!drive_adapter_run_observed(&adapter,span(control),span(action),k_uptime_get()-snapshot.input.received_ms,
  &snapshot.manual_witness,"",&output));assert(output.v_mps>0);
 int64_t original=snapshot.input.received_ms;
 k_sleep(K_MSEC(250));client.connected=false;client.connected=true;
 frames[0]=(struct frame){"xenova/v1/apps/manual/input","drive.input",action,3};
 send_frames(1);
 assert(snapshot.input.seq==2&&snapshot.input.received_ms==original&&client.dropped==1);
 assert(!drive_adapter_run_observed(&adapter,span(control),span(action),k_uptime_get()-original,
  &snapshot.manual_witness,"",&output));assert(!output.v_mps&&!strcmp(output.reason,"INPUT_STALE"));
 frames[0]=(struct frame){"xenova/v1/apps/manual/input","drive.input",neutral,4};
 send_frames(1);assert(snapshot.input.received_ms==original&&client.dropped==2);
 assert(!xd_io_join(k_uptime_get()+1000));
 puts("PASS real receive owner: neutral survives coalescing; duplicate/reversed original samples never refresh age; join succeeds");
 exit(0);
}
