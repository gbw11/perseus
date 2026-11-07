"""Actual input owners, MQTT port and engine; only SDK/Store/peer I/O are fixtures.

No socket/clock/algorithm policy is replaced. Host SDK type shims describe only
the APIs consumed by the real port; they are not an ARM ABI or broker test.
"""
import json
from pathlib import Path
import subprocess

import pytest

from test_input_clock import HOME, RUNTIME, reply, schema, encode
from test_lattice import fixture


MQTT = r'''
#ifndef TEST_MQTT_H
#define TEST_MQTT_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
struct mqtt_utf8 {const uint8_t *utf8;size_t size;};
struct mqtt_topic {struct mqtt_utf8 topic;int qos;};
struct mqtt_publish_param {struct {struct mqtt_topic topic;struct {const void *data;size_t len;} payload;} message;uint16_t message_id;bool retain_flag;};
struct mqtt_subscription_list {struct mqtt_topic *list;uint16_t list_count,message_id;};
struct mqtt_puback_param {uint16_t message_id;};
enum {MQTT_EVT_CONNACK,MQTT_EVT_DISCONNECT,MQTT_EVT_PUBLISH};
struct mqtt_evt {int type,result;union {struct mqtt_publish_param publish;} param;};
struct mqtt_client {struct mqtt_utf8 client_id,*user_name,*password;void *broker;
 void (*evt_cb)(struct mqtt_client *,const struct mqtt_evt *);int protocol_version,clean_session,keepalive;
 uint8_t *rx_buf,*tx_buf;size_t rx_buf_size,tx_buf_size;
 struct {int type;void *custom_transport_data;struct {int sock;} tcp;} transport;};
#define MQTT_QOS_0_AT_MOST_ONCE 0
#define MQTT_QOS_1_AT_LEAST_ONCE 1
#define MQTT_TRANSPORT_CUSTOM 2
#define MQTT_TRANSPORT_NON_SECURE 0
#define MQTT_VERSION_3_1_1 4
void mqtt_client_init(struct mqtt_client *);
int mqtt_connect(struct mqtt_client *);
int mqtt_abort(struct mqtt_client *);
int mqtt_live(struct mqtt_client *);
int mqtt_input(struct mqtt_client *);
int mqtt_keepalive_time_left(struct mqtt_client *);
int mqtt_subscribe(struct mqtt_client *,const struct mqtt_subscription_list *);
int mqtt_publish(struct mqtt_client *,const struct mqtt_publish_param *);
int mqtt_publish_qos1_ack(struct mqtt_client *,const struct mqtt_puback_param *);
int mqtt_readall_publish_payload(struct mqtt_client *,void *,size_t);
int mqtt_read_publish_payload_blocking(struct mqtt_client *,void *,size_t);
#endif
'''

SOCKET = r'''
#include <arpa/inet.h>
#include <netinet/tcp.h>
#include <poll.h>
#define zsock_pollfd pollfd
#define NET_IPPROTO_TCP IPPROTO_TCP
#define ZSOCK_TCP_NODELAY TCP_NODELAY
#define ZSOCK_POLLIN POLLIN
#define ZSOCK_POLLERR POLLERR
#define ZSOCK_POLLHUP POLLHUP
int zsock_poll(struct pollfd *,int,int);
int zsock_setsockopt(int,int,int,const void *,socklen_t);
int zsock_inet_pton(int,const char *,void *);
'''

HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <xenova/drive_runtime.h>
#include "engine.h"
#define XENOVA_LATTICE_APP_H
int drive_lattice_snapshot(dw_state *);
#include "lattice_input.c"
#include "xm_zclient.c"
static struct drive_engine actual_engine;
static struct xt_bootstrap fixture_boot;
static uint8_t wire[DW_WIRE_MAX];static size_t wire_size;
static uint64_t fake_now=100000;
static unsigned iteration,iterations=1,context_calls,polls,inputs_seen,lives,connects,subscribes,publishes,reads,closes,snapshots;
static int source_rc=-EAGAIN;static bool running,world_owner,recover,disconnected,oversize,connack,hard,stop_in_context,recover_closed,recover_link;
static int event_error,payload_error;
static struct xm_zclient *selected;
static struct dw_handle original_handle;
static unsigned contexts[3];
static const char vm[]="11111111-1111-4111-8111-111111111111";
static const char execution[]="22222222-2222-4222-8222-222222222222";
static const char app[]="33333333-3333-4333-8333-333333333333";
static const char host[]="11111111-1111-4111-8111-111111111111";
static const char gateway_id[]="55555555-5555-4555-8555-555555555555";
static const struct dw_trust trust={.vehicle="rc-test-1",.mapper_boot="11111111-1111-4111-8111-111111111111",
 .mapper_instance="22222222-2222-4222-8222-222222222222",.map_epoch="33333333-3333-4333-8333-333333333333",
 .frame_epoch="44444444-4444-4444-8444-444444444444",.odom_epoch="55555555-5555-4555-8555-555555555555",
 .configuration="lattice-lab-1",.calibration="encoder-lab-1",.primitive=TEST_PRIMITIVE_SHA,
 .clock={.epoch="66666666-6666-4666-8666-666666666666",.lower_offset_us=100000,
 .upper_offset_us=101000,.expires_pi_us=3000000,.max_error_us=2000},.max_live_age_us=300000};
int64_t k_uptime_get(void){return (int64_t)(fake_now/1000);}
int xdiag_clock_us(uint64_t *out){*out=fake_now;return 0;}
bool xd_stopping(void){return false;}
int xd_context_get(struct xt_bootstrap *out){
 context_calls++;if(running&&iteration<3)contexts[iteration]++;
 if(running&&(!recover||!iteration)&&source_rc){if(stop_in_context)atomic_set(&stop,1);return source_rc;}
 *out=fixture_boot;if(running&&hard)out->world_profile[0]=0;return 0;
}
int k_mutex_lock(struct k_mutex *m,int wait){(void)m;(void)wait;return 0;}
void k_mutex_unlock(struct k_mutex *m){(void)m;}
void k_sleep(int64_t ms){
 assert(ms==2); /* No cadence/deadline adjustment in the actual owner. */
 if(world_owner&&iteration==0&&source_rc){
  assert(engine->receive_awaiting&&engine->maps.published==0);
  assert(!memcmp(&original_handle,&engine->receive_handle,sizeof(original_handle)));
 }
 if(!world_owner&&iteration==0&&source_rc){
  assert(current_clock.expires_guest==2000000&&application_pending&&probe_sequence==1&&probe_sent==90000);
  assert(!next_probe&&!next_snapshot&&!publishes&&!snapshots);
  for(unsigned k=0;k<2;k++)assert(!atomic_get(&copies[k].state));
 }
 iteration++;fake_now+=2000;if(iteration>=iterations)atomic_set(&stop,1);
}
int32_t k_msleep(int32_t ms){(void)ms;return 0;}
void sys_rand_get(void *out,size_t n){memset(out,42,n);}
struct k_thread *k_thread_create(struct k_thread *t,void *s,size_t n,void (*fn)(void *,void *,void *),void *a,void *b,void *c,int p,int f,int d){
 (void)t;(void)s;(void)n;(void)fn;(void)a;(void)b;(void)c;(void)p;(void)f;(void)d;assert(0);return NULL;
}
int k_thread_join(struct k_thread *t,int64_t d){(void)t;(void)d;return 0;}
void k_thread_name_set(struct k_thread *t,const char *n){(void)t;(void)n;}
int xd_io_binary(const struct xm_zbinary *b){(void)b;return 0;}
int drive_lattice_snapshot(dw_state *copy){(void)copy;snapshots++;return -EAGAIN;}
void mqtt_client_init(struct mqtt_client *m){memset(m,0,sizeof(*m));}
int mqtt_connect(struct mqtt_client *m){connects++;((struct xt_socket *)m->transport.custom_transport_data)->fd=9;return 0;}
int mqtt_abort(struct mqtt_client *m){(void)m;closes++;return 0;}
int xt_socket_close(struct xt_socket *s){s->fd=-1;return s->close_error;}
int mqtt_keepalive_time_left(struct mqtt_client *m){(void)m;return 10000;}
int mqtt_live(struct mqtt_client *m){(void)m;lives++;return -EAGAIN;}
int mqtt_subscribe(struct mqtt_client *m,const struct mqtt_subscription_list *s){(void)m;assert(s->list_count==1);subscribes++;return 0;}
int mqtt_publish(struct mqtt_client *m,const struct mqtt_publish_param *p){(void)m;assert(p->message.payload.len);publishes++;return 0;}
int mqtt_publish_qos1_ack(struct mqtt_client *m,const struct mqtt_puback_param *p){(void)m;(void)p;return 0;}
int mqtt_readall_publish_payload(struct mqtt_client *m,void *out,size_t n){
 (void)m;reads++;if(payload_error)return payload_error;assert(n==wire_size);memcpy(out,wire,n);return 0;
}
int mqtt_read_publish_payload_blocking(struct mqtt_client *m,void *out,size_t n){(void)m;(void)out;(void)n;assert(0);return -EIO;}
int zsock_inet_pton(int af,const char *s,void *out){return inet_pton(af,s,out);}
int zsock_setsockopt(int fd,int level,int name,const void *value,socklen_t n){
 assert(fd==9&&level==NET_IPPROTO_TCP&&name==ZSOCK_TCP_NODELAY&&n==sizeof(int)&&*(const int *)value==1);return 0;
}
int zsock_poll(struct pollfd *fds,int n,int timeout){
 assert(n==1&&timeout==0);polls++;
 if(recover&&iteration&&!recover_link){fds->revents=0;return 0;}
 fds->revents=ZSOCK_POLLIN;return 1;
}
int mqtt_input(struct mqtt_client *m){
 inputs_seen++;
 if(connack){struct mqtt_evt ack={.type=MQTT_EVT_CONNACK};m->evt_cb(m,&ack);}
 if(disconnected){struct mqtt_evt closed={.type=MQTT_EVT_DISCONNECT,.result=-ECONNRESET};m->evt_cb(m,&closed);return 0;}
 struct mqtt_evt event={.type=MQTT_EVT_PUBLISH};event.param.publish.message.topic.topic.utf8=(const uint8_t *)selected->binary.topic;
 event.param.publish.message.topic.topic.size=strlen(selected->binary.topic);
 event.param.publish.message.payload.len=oversize?DW_WIRE_MAX+1:wire_size;
 m->evt_cb(m,&event);return event_error;
}
static void vehicle(unsigned seq,uint64_t at){
 dw_vehicle value={.major=1,.sequence=seq,.source_sample=seq,.source_us=at-100000,.pi_sample_us=at,.clock_error_us=500,
  .quality=3,.position_error_m=.005f,.heading_error_rad=.01f,.has_estimated_steering_rad=true,.estimated_steering_rad=0,
  .has_steering_error_rad=true,.steering_error_rad=.001f};
 strcpy(value.vehicle,"rc-test-1");strcpy(value.host_boot,host);strcpy(value.device_session,app);
 strcpy(value.odom_epoch,trust.odom_epoch);strcpy(value.calibration,"encoder-lab-1");assert(!de_vehicle(engine,&value));
}
int main(int argc,char **argv){
 assert(argc==4);world_owner=!strcmp(argv[1],"map");const char *scenario=argv[2];
 FILE *file=fopen(argv[3],"rb");assert(file);wire_size=fread(wire,1,sizeof(wire),file);fclose(file);assert(wire_size);
 recover_closed=!strcmp(scenario,"recover-closed");recover_link=!strcmp(scenario,"recover-link");
 recover=!strcmp(scenario,"recover")||recover_closed||recover_link;iterations=recover||!strcmp(scenario,"disconnect")?2:1;
 disconnected=!strcmp(scenario,"disconnect");oversize=!strcmp(scenario,"oversize");connack=!strcmp(scenario,"connack");
 hard=!strcmp(scenario,"hard-profile");stop_in_context=!strcmp(scenario,"stop");
 if(!strcmp(scenario,"fresh")||hard)source_rc=0;
 if(!strcmp(scenario,"source-eio"))source_rc=-EIO;
 if(!strcmp(scenario,"source-busy"))source_rc=-EBUSY;
 if(!strcmp(scenario,"source-other"))source_rc=-ENODATA;
 if(!strcmp(scenario,"payload-eio")){payload_error=-EIO;event_error=-EPROTO;}
 if(!strcmp(scenario,"source-copy"))assert(dw_link_decode(wire,wire_size,&incoming)==DW_OK&&
  incoming.which_payload==xenova_drive_DriveLink_accepted_tag);
 engine=&actual_engine;de_init(engine,&lp_selected_config,&lp_selected_assets);assert(!dw_pool_source(&engine->maps,&trust));
 current_clock.trust=trust;current_clock.lower=1000000;current_clock.upper=1100000;current_clock.expires_guest=2000000;
 strcpy(current_clock.gateway,gateway_id);strcpy(gateway,gateway_id);context_available=clock_available=true;
 strcpy(fixture_boot.identity.vm_id,vm);strcpy(fixture_boot.identity.boot_id,host);
 strcpy(fixture_boot.identity.host_boot_id,host);strcpy(fixture_boot.identity.execution_id,execution);
 strcpy(fixture_boot.identity.publisher_id,app);strcpy(fixture_boot.auto_instance_id,app);
 strcpy(fixture_boot.broker_ipv4,"192.0.2.1");fixture_boot.port=1883;
 strcpy(fixture_boot.world_broker_ipv4,"192.0.2.2");fixture_boot.world_port=1883;strcpy(fixture_boot.world_profile,"lattice-lab-1");
 selected=world_owner?&map_client:&link_client;bool unavailable;
 assert(!setup(selected,world_owner?&map_boot:&link_boot,world_owner,&unavailable)&&!unavailable);
 selected->socket_open=strcmp(scenario,"closed")!=0&&!recover_closed;selected->connected=selected->socket_open&&!connack;
 selected->socket_io.fd=selected->socket_open?9:-1;selected->retry_at_ms=recover_closed?0:10000;
 if(!strcmp(scenario,"close-error"))selected->socket_io.close_error=-EIO;
 if(world_owner){
  assert(!de_world(engine,wire,wire_size,1200000));assert(engine->receive_awaiting);
  original_handle=engine->receive_handle;vehicle(1,1100000);vehicle(2,1190000);
 }
 probe_sequence=1;probe_sent=90000;application_pending=true;applied.application_sequence=7;
 uint64_t original_expiry=current_clock.expires_guest;running=true;context_calls=0;
 if(world_owner)maps(NULL,NULL,NULL);else state(NULL,NULL,NULL);
 if(source_rc&&!recover){assert(!accepted_maps&&!engine->maps.published);assert(current_clock.expires_guest==original_expiry);
  assert(application_pending&&probe_sequence==1&&probe_sent==90000&&!next_probe&&!next_snapshot&&!publishes&&!snapshots);
  for(unsigned k=0;k<2;k++)assert(!atomic_get(&copies[k].state));}
 if(world_owner){assert(!engine->receive_pending&&!engine->receive_awaiting);
  if(recover)assert(engine->maps.published==1);
  for(unsigned k=0;k<DW_MAP_SLOTS;k++){
   assert(!engine->maps.slots[k].writing&&!atomic_load(&engine->maps.slots[k].readers));
   for(unsigned r=0;r<8;r++)assert(!atomic_load(&engine->maps.slots[k].leases[r]));
  }
 }
 if(recover_closed)assert(connects==1&&inputs_seen==0&&polls==1&&contexts[0]==1);
 if(recover_link)assert(current_clock.expires_guest==102000+lp_selected_config.clock_lease_us&&reads==2&&rejected_links==1&&probe_sequence==2);
 if(!strcmp(scenario,"closed")||hard||stop_in_context||!strcmp(scenario,"close-error"))assert(!inputs_seen&&!polls&&!connects&&!publishes);
 else if(disconnected)assert(inputs_seen==1&&polls==1&&!connects&&selected->last_io_rc==-ECONNRESET);
 else if(source_rc&&!recover)assert(inputs_seen==1&&polls==1&&!connects);
 if(connack)assert(subscribes==1&&lives==1);
 if(oversize)assert(!reads&&selected->last_io_rc==-EMSGSIZE);
 if(payload_error)assert(selected->last_io_rc==-EIO);
 assert(!selected->socket_open&&!selected->connected&&!atomic_get(&reason));
 printf("{\"polls\":%u,\"inputs\":%u,\"connects\":%u,\"subscribe\":%u,\"live\":%u,\"publish\":%u,\"reads\":%u,\"contexts\":%u,\"first_contexts\":%u,\"maps\":%llu,\"accepted_maps\":%llu,\"dropped\":%u,\"close\":%u,\"rejected_links\":%llu,\"owner_state_bytes\":%zu}\n",
  polls,inputs_seen,connects,subscribes,lives,publishes,reads,context_calls,contexts[0],
  (unsigned long long)engine->maps.published,(unsigned long long)accepted_maps,selected->dropped,closes,(unsigned long long)rejected_links,sizeof(map_input_allowed)+sizeof(link_input_allowed));
 return 0;
}
'''


@pytest.fixture(scope='module')
def owner(tmp_path_factory):
    directory=tmp_path_factory.mktemp('input-transport-owner');inc=directory/'include'
    for name in ('xenova','zephyr/net','zephyr/sys','zephyr/logging','zephyr/random'):(inc/name).mkdir(parents=True,exist_ok=True)
    runtime=RUNTIME[:RUNTIME.index('struct xm_zbinary')]+ '#include "xm_zclient.h"\n'+RUNTIME[RUNTIME.index('int xdiag_clock_us'):]
    runtime=runtime.replace('#define XM_TOPIC_MAX 160\n','')
    runtime='\n'.join(line for line in runtime.split('\n') if not line.startswith(('void xm_zclient_close(', 'int xm_zclient_', 'int mqtt_publish(')))
    (inc/'xenova/drive_runtime.h').write_text(runtime)
    (inc/'xenova/drive_context.h').write_text('#include <xenova/drive_runtime.h>\n')
    (inc/'xenova/mqtt_socket.h').write_text('#ifndef TEST_SOCKET_H\n#define TEST_SOCKET_H\n#include <stdint.h>\nstruct xt_socket {int fd,close_error;int64_t deadline_ms;};\nint xt_socket_close(struct xt_socket *);\n#endif\n')
    (inc/'zephyr/kernel.h').write_text('#include <xenova/drive_runtime.h>\nint32_t k_msleep(int32_t);\n')
    (inc/'zephyr/net/mqtt.h').write_text(MQTT)
    (inc/'zephyr/net/socket.h').write_text(SOCKET)
    (inc/'zephyr/sys/atomic.h').write_text('#include <xenova/drive_runtime.h>\n')
    (inc/'zephyr/sys/util.h').write_text('#include <stddef.h>\n#define CONTAINER_OF(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))\n#define MAX(a,b) ((a)>(b)?(a):(b))\n')
    (inc/'zephyr/random/random.h').write_text('#include <stddef.h>\nvoid sys_rand_get(void *,size_t);\n')
    (inc/'zephyr/logging/log.h').write_text('#define LOG_LEVEL_INF 3\n#define LOG_MODULE_REGISTER(...)\n#define LOG_INF(...) ((void)0)\n#define LOG_WRN(...) ((void)0)\n#define LOG_DBG(...) ((void)0)\n')
    source=directory/'owners.c';source.write_text(HARNESS);binary=directory/'owners'
    world=HOME/'world';mqtt=HOME.parent/'common/xenova-mqtt-c';planner=HOME/'planner'
    sources=[source,world/'world.c',world/'generated/world.pb.c',*(world/'nanopb'/n for n in ('pb_common.c','pb_decode.c','pb_encode.c')),
             planner/'engine.c',planner/'lattice.c',planner/'generated/primitives.c',*sorted((mqtt/'src').glob('*.c'))]
    command=['cc','-std=gnu11','-O1','-g','-Wall','-Wextra','-Werror','-ffunction-sections','-fdata-sections','-Wl,--gc-sections',
             '-fsanitize=address,undefined','-DCONFIG_XENOVA_MQTT_BOUNDED_IO=1','-DCONFIG_XENOVA_MQTT_IO_MS=50',
             '-DCONFIG_XENOVA_MQTT_RX_BUF=2048','-DCONFIG_XENOVA_MQTT_TX_BUF=2048','-DCONFIG_XENOVA_MQTT_MAX_SUBS=4',
             '-DCONFIG_XENOVA_MQTT_LATEST_SLOTS=4','-DCONFIG_XENOVA_MQTT_PUB_TOPICS=4','-DTEST_PRIMITIVE_SHA="'+fixture.PRIMITIVE+'"',
             *['-I'+str(p) for p in (inc,HOME/'zephyr/src',planner,world,world/'generated',world/'nanopb',mqtt/'include',mqtt/'src',mqtt/'ports/zephyr',mqtt/'third_party/jsmn')],
             *map(str,sources),'-lm','-o',str(binary)]
    build=subprocess.run(command,capture_output=True,text=True);assert build.returncode==0,build.stderr
    def run(kind,scenario,wire=None):
        if wire is None:
            if kind=='map':wire=encode(fixture.world())
            else:
                value=reply();value.clock.probe.guest_send_us=90000;value.clock.host_boot=fixture.BOOT
                value.clock.pi_receive_us=1190000;value.clock.pi_send_us=1192000;wire=encode(value)
        path=directory/'wire.bin';path.write_bytes(wire)
        result=subprocess.run([str(binary),kind,scenario,str(path)],capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
        return json.loads(result.stdout)
    return run


@pytest.mark.parametrize('kind',['map','state'])
@pytest.mark.parametrize('scenario',['source-eio','source-busy','source-other','closed','disconnect','connack','oversize','payload-eio','close-error','stop'])
def test_actual_open_transport_pumps_without_adoption_or_new_effects(owner,kind,scenario):
    value=owner(kind,scenario)
    assert value['owner_state_bytes']==2
    assert value['publish']==value['connects']==value['maps']==value['accepted_maps']==0
    assert value['first_contexts']==1


def test_actual_pending_receive_is_preserved_then_adopted_only_after_fresh_setup(owner):
    value=owner('map','recover')
    assert value['polls']==2 and value['inputs']==1 and value['maps']==1 and value['accepted_maps']==0


def test_hard_profile_error_does_not_get_the_soft_source_pump(owner):
    value=owner('map','hard-profile')
    assert value['polls']==value['connects']==value['publish']==value['maps']==0


@pytest.mark.parametrize('kind',['map','state'])
def test_closed_transport_waits_for_fresh_context_before_reconnect(owner,kind):
    value=owner(kind,'recover-closed')
    assert value['connects']==value['polls']==1 and value['first_contexts']==1


def test_clock_decode_is_drained_first_then_adopted_only_after_fresh_context(owner):
    value=owner('state','recover-link')
    assert value['reads']==2 and value['rejected_links']==1 and value['publish']>=1


def test_malformed_link_is_still_decoded_and_drained_without_adoption(owner):
    result=owner('state','source-eio',b'invalid')
    assert result['rejected_links']==result['dropped']==1 and result['publish']==0


def test_complete_state_copy_is_drained_without_adoption(owner):
    original=reply();value=schema.DriveLink(major=1)
    value.accepted.host_boot=fixture.BOOT;value.accepted.gateway_instance=original.clock.gateway_instance
    state=value.accepted.state
    state.CopyFrom(schema.DriveState(major=1,source_vm=original.clock.probe.vm,
        source_execution=original.clock.probe.execution,source_app_instance=original.clock.probe.app_instance,
        source_generation=1,state_revision=1,frame_epoch=original.clock.vehicle.odom_epoch,
        odom_epoch=original.clock.vehicle.odom_epoch,configuration='lattice-lab-1',primitive='a'*64,
        reference_id='road:test',reference_revision=1,path_revision=1,maneuver_revision=1,direction=0,
        maneuver_phase=0,merge_goal=schema.Pose(x=1,y=1,yaw=0),vehicle_sequence=1,calculation_sequence=1,
        tracker_index=0,complete=True,message_id='88888888-8888-4888-8888-888888888888',
        calibration='encoder-lab-1',map_epoch=original.clock.vehicle.odom_epoch,observation_seq=1))
    for x in (0.,1.):state.path.add(pose=schema.Pose(x=x,y=1,yaw=0),steering_rad=0,velocity_mps=.1)
    result=owner('state','source-copy',encode(value))
    assert result['rejected_links']==result['dropped']==1 and result['publish']==0
