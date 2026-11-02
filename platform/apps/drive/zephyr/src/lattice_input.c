/* SPDX-License-Identifier: Apache-2.0 */
/* One Jetson map socket, one Pi state socket, and small vehicle input on the
 * existing Pi control owner. Blocking one connection never waits in a tracker. */
#include "lattice_input.h"
#include "lattice_app.h"
#include "pb_encode.h"
#include <zephyr/sys/atomic.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#include <limits.h>

#define VEHICLE_TOPIC "xenova/v1/vehicle/state.pb"
#define WORLD_TOPIC "xenova/v1/world/snapshot.pb"
extern const struct lp_config lp_selected_config;
extern const struct lp_assets lp_selected_assets;
static struct drive_engine *engine;
static struct xm_zclient map_client,link_client;
static struct xt_bootstrap map_boot,link_boot;
static uint8_t map_wire[DW_WIRE_MAX],link_wire[DW_WIRE_MAX],send_wire[DW_WIRE_MAX],vehicle_wire[2048];
static xenova_drive_DriveLink incoming,outgoing;
struct state_slot {atomic_t state;dw_state copy;};
static struct state_slot copies[2];
static char in_topic[XM_TOPIC_MAX],out_topic[XM_TOPIC_MAX];
static struct k_thread map_thread,link_thread;
K_THREAD_STACK_DEFINE(map_stack,8192);
K_THREAD_STACK_DEFINE(link_stack,8192);
K_MUTEX_DEFINE(input_context_lock);
K_MUTEX_DEFINE(applied_lock);
static struct dl_clock current_clock;
static char gateway[37];
static bool clock_available,context_available;
static atomic_t stop,reason;
static bool map_started,link_started,map_joined,link_joined;
static xenova_drive_AppliedState applied;
static bool application_pending;
static uint64_t probe_sequence,probe_sent,next_probe,next_snapshot;
static uint64_t received_vehicle;
static uint64_t accepted_maps,rejected_maps,rejected_links,rejected_vehicles;
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
static uint64_t clock_trace[16][4];static unsigned clock_trace_count;
#endif

static uint64_t clock_us(void){uint64_t now=0;if(xdiag_clock_us(&now))atomic_set(&reason,DW_TIME);return now;}
static bool stopped(void){return atomic_get(&stop)||xd_stopping();}
void dl_context(const struct xd_inputs *inputs){
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return;
 bool valid=inputs->connected&&!inputs->control.error&&!strcmp(inputs->control.sender,"dom0-gateway")&&inputs->control.instance[0];
 if(!valid||strcmp(gateway,inputs->control.instance)){clock_available=false;gateway[0]=0;}
 context_available=valid;
 if(valid)strcpy(gateway,inputs->control.instance);
 k_mutex_unlock(&input_context_lock);
}
int dl_clock_get(struct dl_clock *out,uint64_t *now_pi){
 uint64_t guest=clock_us();if(!guest||guest>INT64_MAX)return -EIO;
 if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
 int rc=context_available&&clock_available&&guest<=current_clock.expires_guest?0:-EAGAIN;
 if(!rc)*out=current_clock;
 k_mutex_unlock(&input_context_lock);
 if(!rc){
  if((out->upper>0&&(uint64_t)out->upper>INT64_MAX-guest)||(int64_t)guest+out->upper<0)return -ERANGE;
  *now_pi=(uint64_t)((int64_t)guest+out->upper);
 }
 return rc;
}
static int vehicle_received(void *unused,const uint8_t *bytes,size_t count,int64_t received){
 ARG_UNUSED(unused);ARG_UNUSED(received);
 dw_vehicle sample;enum dw_result decoded=dw_vehicle_decode(bytes,count,&sample);
 struct dl_clock mapping;uint64_t now;
 struct xt_bootstrap boot;
 if(decoded||dl_clock_get(&mapping,&now)||xd_context_get(&boot)||
   strcmp(sample.host_boot,boot.identity.host_boot_id)||!sample.has_gateway_instance||strcmp(sample.gateway_instance,mapping.gateway)||
   strcmp(sample.vehicle,mapping.trust.vehicle)||strcmp(sample.odom_epoch,mapping.trust.odom_epoch)||
   strcmp(sample.calibration,mapping.trust.calibration)||sample.pi_sample_us<=received_vehicle||
   now<sample.pi_sample_us||now-sample.pi_sample_us+sample.clock_error_us>lp_selected_config.pose_age_us){rejected_vehicles++;return -EPROTO;}
 uint64_t width=(uint64_t)mapping.upper-(uint64_t)mapping.lower;
 if(width>UINT32_MAX-sample.clock_error_us){rejected_vehicles++;return -ERANGE;}
 sample.clock_error_us+=(uint32_t)width;
 enum dw_result rc=dw_odom_add(&engine->odometry,&sample);
 if(rc==DW_EPOCH){
  /* A new epoch is already authorized by the current clock/trust reply.
   * Atomic reset invalidates old map anchors; it does not reset AUTO or MANUAL. */
  rc=dw_odom_reset(&engine->odometry);if(!rc)rc=dw_odom_add(&engine->odometry,&sample);
 }
 if(rc){rejected_vehicles++;return -EAGAIN;}
 received_vehicle=sample.pi_sample_us;return 0;
}
static int map_received(void *unused,const uint8_t *bytes,size_t count,int64_t received){
 ARG_UNUSED(unused);ARG_UNUSED(received);struct dl_clock mapping;uint64_t now;
 if(dl_clock_get(&mapping,&now)){rejected_maps++;return -EAGAIN;}
 if(memcmp(&engine->maps.trust,&mapping.trust,sizeof(mapping.trust))){
  if(dw_pool_source(&engine->maps,&mapping.trust)){rejected_maps++;return -EBUSY;}
 }
 enum de_result rc=de_world(engine,bytes,count,now);
 if(rc){rejected_maps++;atomic_set(&reason,rc);return -EPROTO;}
 accepted_maps++;return 0;
}
static bool same_identity(const struct xt_bootstrap *a,const struct xt_bootstrap *b){
 return !memcmp(&a->identity,&b->identity,sizeof(a->identity));
}
static int link_received(void *unused,const uint8_t *bytes,size_t count,int64_t received){
 ARG_UNUSED(unused);ARG_UNUSED(received);
 if(dw_link_decode(bytes,count,&incoming)){rejected_links++;return -EPROTO;}
 struct xt_bootstrap boot;if(xd_context_get(&boot)){rejected_links++;return -EAGAIN;}
 if(incoming.which_payload==xenova_drive_DriveLink_clock_tag){
  const xenova_drive_ClockReply *reply=&incoming.payload.clock;const xenova_drive_ClockProbe *probe=&reply->probe;
  uint64_t now=clock_us();
  if(!probe_sent||probe->sequence!=probe_sequence||probe->guest_send_us!=probe_sent||now<probe_sent||
   strcmp(probe->vm,boot.identity.vm_id)||strcmp(probe->execution,boot.identity.execution_id)||
   strcmp(probe->app_instance,boot.auto_instance_id)||strcmp(reply->host_boot,boot.identity.host_boot_id)||
   reply->pi_receive_us>reply->pi_send_us||reply->pi_send_us>INT64_MAX||now>INT64_MAX||
   strcmp(reply->vehicle.vehicle,lp_selected_config.vehicle)||strcmp(reply->vehicle.calibration,lp_selected_config.calibration)||
   (reply->has_trust&&(strcmp(reply->trust.configuration,lp_selected_config.id)||strcmp(reply->trust.primitive,lp_selected_assets.sha256)))){rejected_links++;return -EPROTO;}
  int64_t lower=(int64_t)reply->pi_send_us-(int64_t)now;
  int64_t upper=(int64_t)reply->pi_receive_us-(int64_t)probe_sent;
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
  if(clock_trace_count<16){uint64_t *row=clock_trace[clock_trace_count++];row[0]=probe_sent;row[1]=now;row[2]=reply->pi_receive_us;row[3]=reply->pi_send_us;}
#endif
  if(upper<lower||(uint64_t)upper-(uint64_t)lower>lp_selected_config.clock_error_us){rejected_links++;return -ETIME;}
  if(k_mutex_lock(&input_context_lock,K_NO_WAIT))return -EBUSY;
  if(!context_available||strcmp(gateway,reply->gateway_instance)){k_mutex_unlock(&input_context_lock);return -EXDEV;}
  struct dl_clock *out=&current_clock;memset(out,0,sizeof(*out));out->lower=lower;out->upper=upper;
  out->expires_guest=now+lp_selected_config.clock_lease_us;strcpy(out->gateway,gateway);
  const xenova_drive_WorldTrust *t=&reply->trust;struct dw_trust *d=&out->trust;
  strcpy(d->vehicle,reply->vehicle.vehicle);strcpy(d->mapper_boot,t->mapper_boot);strcpy(d->mapper_instance,t->mapper_instance);
  strcpy(d->map_epoch,t->map_epoch);strcpy(d->frame_epoch,t->frame_epoch);strcpy(d->odom_epoch,reply->vehicle.odom_epoch);
  strcpy(d->configuration,t->configuration);strcpy(d->calibration,reply->vehicle.calibration);strcpy(d->primitive,t->primitive);
  strcpy(d->clock.epoch,t->clock_epoch);d->clock.lower_offset_us=t->lower_offset_us;d->clock.upper_offset_us=t->upper_offset_us;
  d->clock.expires_pi_us=t->expires_pi_us;d->clock.max_error_us=MIN(t->max_error_us,lp_selected_config.clock_error_us);d->max_live_age_us=lp_selected_config.live_age_us;
  clock_available=true;k_mutex_unlock(&input_context_lock);probe_sent=0;return 0;
 }
 if(incoming.which_payload==xenova_drive_DriveLink_accepted_tag){
  struct dl_clock mapping;uint64_t now;const xenova_drive_AcceptedState *copy=&incoming.payload.accepted;
  if(dl_clock_get(&mapping,&now)||strcmp(copy->host_boot,boot.identity.host_boot_id)||strcmp(copy->gateway_instance,mapping.gateway))return -EXDEV;
  for(unsigned k=0;k<2;k++)if(atomic_cas(&copies[k].state,0,1)){
   copies[k].copy=copy->state;atomic_set(&copies[k].state,2);return 0;
  }
  return -ENOBUFS;
 }
 rejected_links++;return -EPROTO;
}
int dl_state_take(const dw_state **out){
 for(unsigned k=0;k<2;k++)if(atomic_cas(&copies[k].state,2,3)){*out=&copies[k].copy;return (int)k;}
 return -EAGAIN;
}
void dl_state_release(int slot){if(slot>=0&&slot<2)(void)atomic_cas(&copies[slot].state,3,0);}
int dl_applied(const xenova_drive_AppliedState *value){
 if(stopped())return -ESHUTDOWN;
 if(k_mutex_lock(&applied_lock,K_NO_WAIT))return -EBUSY;
 applied=*value;application_pending=true;k_mutex_unlock(&applied_lock);return 0;
}
static int binary_publish(struct xm_zclient *client,const char *topic,const xenova_drive_DriveLink *value){
 pb_ostream_t stream=pb_ostream_from_buffer(send_wire,sizeof(send_wire));
 if(!pb_encode(&stream,xenova_drive_DriveLink_fields,value))return -EMSGSIZE;
 struct mqtt_publish_param p={0};p.message.topic.topic.utf8=(const uint8_t *)topic;p.message.topic.topic.size=strlen(topic);
 p.message.topic.qos=MQTT_QOS_0_AT_MOST_ONCE;p.message.payload.data=send_wire;p.message.payload.len=stream.bytes_written;
#if defined(CONFIG_XENOVA_MQTT_BOUNDED_IO)
 client->socket_io.deadline_ms=k_uptime_get()+CONFIG_XENOVA_MQTT_IO_MS;
#endif
 return mqtt_publish(&client->mqtt,&p);
}
static int setup(struct xm_zclient *client,struct xt_bootstrap *saved,bool world){
 struct xt_bootstrap boot;int rc=xd_context_get(&boot);if(rc)return rc;
 if(world&&(!boot.world_broker_ipv4[0]||strcmp(boot.world_profile,lp_selected_config.id)))return -ENODATA;
 if(saved->identity.vm_id[0]&&same_identity(saved,&boot)&&
   (!world||(!strcmp(saved->world_broker_ipv4,boot.world_broker_ipv4)&&saved->world_port==boot.world_port&&
    !strcmp(saved->world_username,boot.world_username)&&!strcmp(saved->world_password,boot.world_password))))return 0;
 if(saved->identity.vm_id[0])xm_zclient_close(client);
 *saved=boot;
 struct xm_zconfig cfg={.host=world?saved->world_broker_ipv4:saved->broker_ipv4,.port=world?saved->world_port:saved->port,
  .username=world?saved->world_username:saved->username,.password=world?saved->world_password:saved->password,
  .boot_id=saved->identity.boot_id,.instance_id=saved->identity.publisher_id,.keepalive_s=10};
 rc=xm_zclient_init(client,saved->identity.vm_id,NULL,world?"drive-world":"drive-state",&cfg);
 if(rc){saved->identity.vm_id[0]=0;return rc;}
 /* Client connection labels are not app/execution identities. */
 snprintf(client->client_id_buf,sizeof(client->client_id_buf),"%s-%s-%.8s",saved->identity.vm_id,world?"world":"state",saved->identity.publisher_id);
 client->client_id.size=strlen(client->client_id_buf);
 client->mqtt.client_id=client->client_id;
 if(world)client->binary=(struct xm_zbinary){WORLD_TOPIC,map_wire,sizeof(map_wire),map_received,NULL};
 else{
  snprintf(in_topic,sizeof(in_topic),"xenova/v1/vms/%s/drive/link/in",saved->identity.vm_id);
  snprintf(out_topic,sizeof(out_topic),"xenova/v1/vms/%s/drive/link/out",saved->identity.vm_id);
  client->binary=(struct xm_zbinary){in_topic,link_wire,sizeof(link_wire),link_received,NULL};
 }
 rc=xm_zclient_subscribe(client,world?WORLD_TOPIC:in_topic,0);
 if(rc){xm_zclient_close(client);saved->identity.vm_id[0]=0;}
 return rc;
}
static void maps(void *a,void *b,void *c){
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 while(!stopped()){
  if(!setup(&map_client,&map_boot,true))(void)xm_zclient_poll(&map_client,0);
  struct dl_clock mapping;uint64_t now;
  if(!dl_clock_get(&mapping,&now))(void)de_receive_poll(engine,now);
  k_sleep(K_MSEC(2));
 }
 xm_zclient_close(&map_client);
 while(de_receive_drain(engine)==DE_BUSY)k_sleep(K_MSEC(1));
 memset(&map_boot,0,sizeof(map_boot));
}
static void state(void *a,void *b,void *c){
 ARG_UNUSED(a);ARG_UNUSED(b);ARG_UNUSED(c);
 while(!stopped()){
  if(!setup(&link_client,&link_boot,false)){
   (void)xm_zclient_poll(&link_client,0);uint64_t now=clock_us();
   if(link_client.connected&&now>=next_probe){
    struct xt_bootstrap boot;
    if(!xd_context_get(&boot)&&probe_sequence<UINT64_MAX){
     memset(&outgoing,0,sizeof(outgoing));outgoing.major=1;outgoing.which_payload=xenova_drive_DriveLink_probe_tag;
     xenova_drive_ClockProbe *p=&outgoing.payload.probe;strcpy(p->vm,boot.identity.vm_id);
     strcpy(p->execution,boot.identity.execution_id);strcpy(p->app_instance,boot.auto_instance_id);
     p->sequence=++probe_sequence;p->guest_send_us=now;probe_sent=now;
     (void)binary_publish(&link_client,out_topic,&outgoing);next_probe=now+lp_selected_config.clock_lease_us/2;
    }
   }
   if(link_client.connected&&!k_mutex_lock(&applied_lock,K_NO_WAIT)){
    bool send=application_pending;
    if(send){memset(&outgoing,0,sizeof(outgoing));outgoing.major=1;outgoing.which_payload=xenova_drive_DriveLink_applied_tag;
     outgoing.payload.applied=applied;application_pending=false;}
    k_mutex_unlock(&applied_lock);
    if(send)(void)binary_publish(&link_client,out_topic,&outgoing);
   }
   if(link_client.connected&&now>=next_snapshot){
    memset(&outgoing,0,sizeof(outgoing));outgoing.major=1;outgoing.which_payload=xenova_drive_DriveLink_snapshot_tag;
    if(!drive_lattice_snapshot(&outgoing.payload.snapshot))(void)binary_publish(&link_client,out_topic,&outgoing);
    next_snapshot=now+lp_selected_config.state_period_us;
   }
  }
  k_sleep(K_MSEC(2));
 }
 xm_zclient_close(&link_client);memset(&link_boot,0,sizeof(link_boot));
}
int dl_start(struct drive_engine *value){
 if(engine||!value)return -EALREADY;
 engine=value;
 struct xm_zbinary binary={VEHICLE_TOPIC,vehicle_wire,sizeof(vehicle_wire),vehicle_received,NULL};
 int rc=xd_io_binary(&binary);if(rc)return rc;
 map_started=k_thread_create(&map_thread,map_stack,K_THREAD_STACK_SIZEOF(map_stack),maps,NULL,NULL,NULL,13,0,K_NO_WAIT)!=NULL;
 if(!map_started)return -ENOMEM;
 link_started=k_thread_create(&link_thread,link_stack,K_THREAD_STACK_SIZEOF(link_stack),state,NULL,NULL,NULL,12,0,K_NO_WAIT)!=NULL;
 if(!link_started)return -ENOMEM;
 k_thread_name_set(&map_thread,"drive-world");k_thread_name_set(&link_thread,"drive-state");return 0;
}
int dl_join(int64_t deadline){
 atomic_set(&stop,1);int rc=0;
 struct k_thread *threads[]={&map_thread,&link_thread};bool started[]={map_started,link_started};bool *joined[]={&map_joined,&link_joined};
 for(unsigned k=0;k<2;k++)if(started[k]&&!*joined[k]){
  int64_t left=deadline-k_uptime_get();int status=k_thread_join(threads[k],left>0?K_MSEC(left):K_NO_WAIT);
  if(status)rc=-ETIMEDOUT;else *joined[k]=true;
 }
 return rc;
}
const char *dl_status(void){return atomic_get(&reason)?"INPUT_OBSERVATION_FAILED":accepted_maps?"INPUT_AVAILABLE":"WORLD_UNAVAILABLE";}
void dl_report(void){
 printf("XD_WORLD {\"accepted_maps\":%llu,\"rejected_maps\":%llu,\"rejected_links\":%llu,\"rejected_vehicles\":%llu,\"clock\":%d,\"context\":%d,\"vehicle_us\":%llu,\"map_reason\":\"%s\"}\n",
  (unsigned long long)accepted_maps,(unsigned long long)rejected_maps,(unsigned long long)rejected_links,
  (unsigned long long)rejected_vehicles,clock_available,context_available,(unsigned long long)received_vehicle,
  dw_reason(atomic_load(&engine->last_world_result)));
 printf("XD_BINARY_IO {\"map_received\":%u,\"map_dropped\":%u,\"map_rc\":%d,\"state_received\":%u,\"state_dropped\":%u,\"state_rc\":%d,\"offset_lower\":%lld,\"offset_upper\":%lld}\n",
  map_client.received,map_client.dropped,map_client.last_io_rc,link_client.received,link_client.dropped,link_client.last_io_rc,
  (long long)current_clock.lower,(long long)current_clock.upper);
#if defined(CONFIG_XENOVA_DRIVE_NATIVE_TEST)
 for(unsigned k=0;k<clock_trace_count;k++)printf("XD_CLOCK [%llu,%llu,%llu,%llu]\n",
  (unsigned long long)clock_trace[k][0],(unsigned long long)clock_trace[k][1],
  (unsigned long long)clock_trace[k][2],(unsigned long long)clock_trace[k][3]);
#endif
}
