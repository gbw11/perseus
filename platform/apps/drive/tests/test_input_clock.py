"""Actual C input callback + nanopb decoder, with explicit external RTOS stubs.

The complete product lattice_input.c is compiled, not a copied comparison.
Clock/context/mutex/MQTT execution are local fixtures. Logging drop/filter are
test sinks, not proof that a real Zephyr backend drained a saturated buffer.
"""
import json
from pathlib import Path
import subprocess
import sys

import pytest

HOME=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HOME/'world/python'))
from xenova_drive_world import schema,encode


RUNTIME=r'''
#ifndef TEST_RUNTIME_H
#define TEST_RUNTIME_H
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <xenova/mqtt/xm.h>
#define ARG_UNUSED(x) (void)(x)
#define MIN(a,b) ((a)<(b)?(a):(b))
#define K_NO_WAIT 0
#define K_MSEC(x) (x)
#define K_THREAD_STACK_SIZEOF(x) sizeof(x)
#define K_THREAD_STACK_DEFINE(name,n) static unsigned char name[n]
#define K_MUTEX_DEFINE(name) struct k_mutex name
#define MQTT_QOS_0_AT_MOST_ONCE 0
typedef long atomic_t;
static inline long atomic_get(atomic_t *v){return *v;}
static inline void atomic_set(atomic_t *v,long n){*v=n;}
static inline int atomic_cas(atomic_t *v,long a,long b){if(*v!=a)return 0;*v=b;return 1;}
struct k_mutex {int unused;};struct k_thread {int unused;};
struct test_identity {char vm_id[37],boot_id[37],host_boot_id[37],execution_id[37],publisher_id[37];};
struct xt_bootstrap {struct test_identity identity;char auto_instance_id[37],manual_instance_id[37],world_broker_ipv4[32],broker_ipv4[32],
 world_profile[65],world_username[65],world_password[65],username[65],password[65];int world_port,port;};
struct xd_inputs {bool connected;struct {int error;char sender[65],instance[37];
 char payload[XM_MAX_DRIVING_BYTES+1];size_t length;} control;};
struct xm_zbinary {const char *topic;uint8_t *buffer;size_t capacity;
 int (*receive)(void *,const uint8_t *,size_t,int64_t);void *context;};
struct mqtt_utf8 {const uint8_t *utf8;size_t size;};
struct mqtt_client {struct mqtt_utf8 client_id;};
struct mqtt_publish_param {struct {struct {struct mqtt_utf8 topic;int qos;} topic;
 struct {const void *data;size_t len;} payload;} message;};
struct xm_zclient {struct {int64_t deadline_ms;} socket_io;struct mqtt_client mqtt;
 struct mqtt_utf8 client_id;char client_id_buf[128];struct xm_zbinary binary;bool socket_open,connected;
 unsigned received,dropped;int last_io_rc;};
struct xm_zconfig {const char *host,*username,*password,*boot_id,*instance_id;int port,keepalive_s;};
int xdiag_clock_us(uint64_t *);
int xd_context_get(struct xt_bootstrap *);
int k_mutex_lock(struct k_mutex *,int);
void k_mutex_unlock(struct k_mutex *);
int64_t k_uptime_get(void);
bool xd_stopping(void);
int xd_io_binary(const struct xm_zbinary *);
void xm_zclient_close(struct xm_zclient *);
int xm_zclient_init(struct xm_zclient *,const char *,const char *,const char *,const struct xm_zconfig *);
int xm_zclient_subscribe(struct xm_zclient *,const char *,int);
int xm_zclient_poll(struct xm_zclient *,int);
int mqtt_publish(struct mqtt_client *,const struct mqtt_publish_param *);
struct k_thread *k_thread_create(struct k_thread *,void *,size_t,void (*)(void *,void *,void *),void *,void *,void *,int,int,int);
int k_thread_join(struct k_thread *,int64_t);
void k_thread_name_set(struct k_thread *,const char *);
void k_sleep(int64_t);
#endif
'''

HARNESS=r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <xenova/drive_runtime.h>
#include "engine.h"
#define XENOVA_LATTICE_APP_H
int drive_lattice_snapshot(dw_state *);
static int test_log_filtered;
void test_log(const char *,...);
#include "lattice_input.c"
static uint64_t fake_now=1010000;
static unsigned clock_calls,clock_fail_call;
static int context_rc,mutex_rc,world_rc,drop_logs;
static unsigned lock_depth,log_calls,emitted,clock_rows,map_rows,last_phase,last_map_phase;
static int map_actual,map_return;
static uint64_t logged_t4,logged_decision;
static struct drive_engine fixture_engine;
static const char vm[]="11111111-1111-4111-8111-111111111111";
static const char execution[]="22222222-2222-4222-8222-222222222222";
static const char instance[]="33333333-3333-4333-8333-333333333333";
static const char host[]="44444444-4444-4444-8444-444444444444";
static const char gateway_id[]="55555555-5555-4555-8555-555555555555";
const struct lp_config lp_selected_config={.id="lattice-lab-1",.vehicle="rc-test-1",.calibration="encoder-lab-1",
 .live_age_us=300000,.clock_error_us=20000,.clock_lease_us=500000};
const struct lp_assets lp_selected_assets={.sha256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"};
int xdiag_clock_us(uint64_t *out){clock_calls++;if(clock_calls==clock_fail_call)return -EIO;*out=fake_now;return 0;}
int xd_context_get(struct xt_bootstrap *out){
 fake_now+=10000;if(context_rc)return context_rc;memset(out,0,sizeof(*out));
 strcpy(out->identity.vm_id,vm);strcpy(out->identity.execution_id,execution);
 strcpy(out->identity.host_boot_id,host);strcpy(out->auto_instance_id,instance);return 0;
}
int k_mutex_lock(struct k_mutex *m,int timeout){(void)m;(void)timeout;if(mutex_rc)return mutex_rc;lock_depth++;return 0;}
void k_mutex_unlock(struct k_mutex *m){(void)m;assert(lock_depth);lock_depth--;}
enum de_result de_world(struct drive_engine *e,const uint8_t *b,size_t n,uint64_t now){
 (void)e;(void)b;(void)n;(void)now;return (enum de_result)world_rc;
}
enum dw_result __real_dw_link_decode(const uint8_t *,size_t,xenova_drive_DriveLink *);
enum dw_result __wrap_dw_link_decode(const uint8_t *b,size_t n,xenova_drive_DriveLink *out){
 enum dw_result rc=__real_dw_link_decode(b,n,out);fake_now+=25000;return rc;
}
void test_log(const char *fmt,...){
 assert(!lock_depth);log_calls++;if(!drop_logs)emitted++;
 va_list args;va_start(args,fmt);
 if(!strncmp(fmt,"XD_CLOCK_DIAG",13)){
  clock_rows++;(void)va_arg(args,unsigned);
  for(int k=0;k<4;k++)(void)va_arg(args,unsigned long long);
  logged_t4=va_arg(args,unsigned long long);logged_decision=va_arg(args,unsigned long long);
  (void)va_arg(args,int);(void)va_arg(args,int);(void)va_arg(args,int);last_phase=va_arg(args,unsigned);
 }else{
  assert(!strncmp(fmt,"XD_MAP_DIAG",11));map_rows++;
  (void)va_arg(args,unsigned);(void)va_arg(args,unsigned);
  map_actual=va_arg(args,int);map_return=va_arg(args,int);last_map_phase=va_arg(args,unsigned);
 }
 va_end(args);
}
int main(int argc,char **argv){
 assert(argc==3);uint8_t wire[DW_WIRE_MAX];FILE *f=fopen(argv[1],"rb");assert(f);
 size_t length=fread(wire,1,sizeof(wire),f);fclose(f);const char *scenario=argv[2];
 engine=&fixture_engine;dw_pool_init(&engine->maps);context_available=true;strcpy(gateway,gateway_id);
 probe_sent=1000000;auto_probe_sequence=1;
 if(!strcmp(scenario,"network-slow"))fake_now=1040001;
 if(!strcmp(scenario,"entry-failure"))clock_fail_call=1;
 if(!strcmp(scenario,"nonclock-entry-failure")){
  clock_fail_call=1;clock_available=true;current_clock.lower=4993000;current_clock.upper=5002000;
  current_clock.expires_guest=1510000;strcpy(current_clock.gateway,gateway_id);
  strcpy(current_clock.vm,vm);strcpy(current_clock.execution,execution);strcpy(current_clock.app_instance,instance);strcpy(current_clock.host_boot,host);
 }
 if(!strcmp(scenario,"diag-failure"))clock_fail_call=2;
 if(!strcmp(scenario,"context-failure")){context_rc=-EAGAIN;clock_fail_call=1;}
 if(!strcmp(scenario,"mutex-busy"))mutex_rc=-EBUSY;
 if(!strcmp(scenario,"context-change"))strcpy(gateway,"changed");
 if(!strcmp(scenario,"filtered"))test_log_filtered=1;
 if(!strcmp(scenario,"drop"))drop_logs=1;
 int rc=link_received(NULL,wire,length,INT64_MAX);uint64_t expiry=current_clock.expires_guest;
 unsigned accepted=rc==0;
 if(!strcmp(scenario,"cap")||!strcmp(scenario,"filtered")||!strcmp(scenario,"drop")){
  for(unsigned k=1;k<50;k++){
   fake_now=1010000;probe_sent=1000000;auto_probe_sequence=1;
   assert(link_received(NULL,wire,length,INT64_MAX)==0);accepted++;
  }
  memcpy(&engine->maps.trust,&current_clock.trust,sizeof(current_clock.trust));
  for(unsigned k=0;k<20;k++)assert(map_received(NULL,wire,length,INT64_MAX)==0);
 }
 if(!strcmp(scenario,"map-world-failure")){
  memcpy(&engine->maps.trust,&current_clock.trust,sizeof(current_clock.trust));world_rc=DE_LIMIT;
  rc=map_received(NULL,wire,length,INT64_MAX);
 }
 if(!strcmp(scenario,"map-clock-failure")){
  clock_available=false;rc=map_received(NULL,wire,length,INT64_MAX);
 }
 if(!strcmp(scenario,"map-lock-busy")){
  mutex_rc=-EBUSY;rc=map_received(NULL,wire,length,INT64_MAX);
 }
 if(!strcmp(scenario,"map-source-busy")){
  atomic_flag_test_and_set(&engine->maps.lock);rc=map_received(NULL,wire,length,INT64_MAX);
  atomic_flag_clear(&engine->maps.lock);
 }
 int context_guard=0;
 if(!strncmp(scenario,"observed-",9)){
  struct xd_inputs observed={.connected=true};
  strcpy(observed.control.sender,"dom0-gateway");strcpy(observed.control.instance,gateway_id);
  if(!strcmp(scenario,"observed-wrong-sender"))strcpy(observed.control.sender,"gateway");
  if(!strcmp(scenario,"observed-disconnect"))observed.connected=false;
  if(!strcmp(scenario,"observed-control-busy"))observed.control.error=-EBUSY;
  if(!strcmp(scenario,"observed-control-error"))observed.control.error=-EIO;
  if(!strcmp(scenario,"observed-new-gateway"))strcpy(observed.control.instance,"88888888-8888-4888-8888-888888888888");
  dl_context(&observed);
  struct dl_clock held;uint64_t mapped=0;context_guard=dl_clock_get(&held,&mapped);
 }
 uint64_t lower=(uint64_t)current_clock.lower,upper=(uint64_t)current_clock.upper;
 unsigned before_guard=clock_calls;fake_now=1510001;struct dl_clock value;uint64_t mapped=0;
 int expired=dl_clock_get(&value,&mapped);
 unsigned cq=0,mq=0;
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
 cq=clock_diag_count;mq=map_diag_count;
#endif
 printf("{\"rc\":%d,\"accepted\":%u,\"lower\":%llu,\"upper\":%llu,\"expiry\":%llu,"
  "\"expired_guard\":%d,\"reason\":%ld,\"clock_calls\":%u,\"calls_before_guard\":%u,"
  "\"clock_rows\":%u,\"map_rows\":%u,\"log_calls\":%u,\"emitted\":%u,"
  "\"clock_quota\":%u,\"map_quota\":%u,\"last_phase\":%u,\"last_map_phase\":%u,\"map_actual\":%d,\"map_return\":%d,"
  "\"logged_t4\":%llu,\"logged_decision\":%llu,\"context_guard\":%d}\n",
  rc,accepted,(unsigned long long)lower,(unsigned long long)upper,(unsigned long long)expiry,expired,
  reason,clock_calls,before_guard,clock_rows,map_rows,log_calls,emitted,cq,mq,last_phase,last_map_phase,map_actual,map_return,
  (unsigned long long)logged_t4,(unsigned long long)logged_decision,context_guard);
 return 0;
}
'''


def reply(*,map_trust=False):
    body=schema.DriveLink(major=2).clock
    body.probe.CopyFrom(schema.ClockProbe(vm='11111111-1111-4111-8111-111111111111',
        execution='22222222-2222-4222-8222-222222222222',app_instance='33333333-3333-4333-8333-333333333333',sequence=1,guest_send_us=1000000))
    body.host_boot='44444444-4444-4444-8444-444444444444';body.gateway_instance='55555555-5555-4555-8555-555555555555'
    body.pi_receive_us=6002000;body.pi_send_us=6003000
    body.vehicle.CopyFrom(schema.VehicleSample(major=1,vehicle='rc-test-1',host_boot=body.host_boot,
        device_session='66666666-6666-4666-8666-666666666666',odom_epoch='77777777-7777-4777-8777-777777777777',
        sequence=1,source_sample=1,source_us=1000000,pi_sample_us=6000000,clock_error_us=500,left_ticks=0,right_ticks=0,
        odom_pose=schema.Pose(x=0,y=0,yaw=0),velocity_mps=0,position_error_m=.003,heading_error_rad=.003,
        quality=3,calibration='encoder-lab-1'))
    if map_trust:
        # Explicit map-gate fixture; retain the original clock-only reply elsewhere.
        body.trust.CopyFrom(schema.WorldTrust(vehicle=body.vehicle.vehicle,
            mapper_boot='11111111-1111-4111-8111-111111111111',
            mapper_instance='22222222-2222-4222-8222-222222222222',
            map_epoch='33333333-3333-4333-8333-333333333333',
            frame_epoch='44444444-4444-4444-8444-444444444444',
            odom_epoch=body.vehicle.odom_epoch,configuration='lattice-lab-1',
            calibration=body.vehicle.calibration,primitive='a'*64,
            clock_epoch='66666666-6666-4666-8666-666666666666',
            lower_offset_us=0,upper_offset_us=0,
            expires_pi_us=body.pi_send_us+500000,max_error_us=20000))
    value=schema.DriveLink(major=2);value.clock.CopyFrom(body);return value


def complete_state2_metadata(state):
    """기존 clock/transport fixture의 전진 NONE 후보를 새 공용 형태로 완성한다."""
    state.state_features=7
    state.mission.CopyFrom(schema.MissionExecution(mission_id=bytes(16),mission_revision=0,kind=0,phase=0,
        result=0,operator_request_nonce=bytes(16),mission_deadline_pi_us=0,cancel_fence_revision=0,
        active_segment=0,scene_admission_id=bytes(16),scene_admission_revision=0,strategy=0,
        execution_deadline_pi_us=0,correction_attempts_used=0,execution_travel_m=0.,grant_revision=0,
        search_travel_m=0.,search_deadline_pi_us=0))
    state.segments.add(first_index=0,last_index=1,signed_direction=1)
    state.direction_gate.CopyFrom(schema.DirectionGateRef(gate_revision=0,stop_command_sequence=0,
        state=0,requested_direction=0))


@pytest.fixture(scope='module',params=[False,True],ids=['diagnostics-off','diagnostics-on'])
def native(request,tmp_path_factory):
    directory=tmp_path_factory.mktemp('input-clock-native');include=directory/'include'
    for name in ('xenova','zephyr/sys','zephyr/logging'):(include/name).mkdir(parents=True,exist_ok=True)
    (include/'xenova/drive_runtime.h').write_text(RUNTIME)
    (include/'xenova/drive_context.h').write_text('#include <xenova/drive_runtime.h>\n')
    (include/'zephyr/sys/atomic.h').write_text('#include <xenova/drive_runtime.h>\n')
    (include/'zephyr/logging/log.h').write_text('#define LOG_LEVEL_INF 3\n#define LOG_MODULE_REGISTER(...)\n#define LOG_INF(...) do { if(!test_log_filtered) test_log(__VA_ARGS__); } while(0)\n')
    source=directory/'input.c';source.write_text(HARNESS);mqtt=HOME.parent/'common/xenova-mqtt-c';world=HOME/'world';binary=directory/'input-clock'
    args=['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-ffunction-sections','-fdata-sections',
        '-Wl,--gc-sections','-Wl,--wrap=dw_link_decode','-fsanitize=undefined',
        *(['-DCONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS=1'] if request.param else []),
        *['-I'+str(p) for p in (include,HOME/'zephyr/src',HOME/'planner',world,world/'generated',world/'nanopb',mqtt/'include',mqtt/'third_party/jsmn',HOME.parent/'common/xenova-drive-runtime/include')],
        str(source),str(world/'world.c'),str(world/'generated/world.pb.c'),*[str(p) for p in sorted((mqtt/'src').glob('*.c'))],
        *[str(world/'nanopb'/name) for name in ('pb_common.c','pb_decode.c','pb_encode.c')],'-lm','-o',str(binary)]
    build=subprocess.run(args,capture_output=True,text=True)
    assert build.returncode==0,build.stderr
    def run(scenario='normal',value=None,wire=None):
        map_trust=scenario in ('cap','drop','filtered','map-world-failure','map-source-busy')
        path=directory/'input.bin';path.write_bytes(wire if wire is not None else encode(value if value is not None else reply(map_trust=map_trust)))
        result=subprocess.run([str(binary),str(path),scenario],capture_output=True,text=True,check=True)
        assert not result.stderr
        return json.loads(result.stdout)
    return request.param,run


def test_entry_clock_excludes_known_decode_context_work_without_renewing_lease(native):
    enabled,run=native;result=run()
    assert result['rc']==0 and result['accepted']==1
    assert (result['lower'],result['upper'])==(4993000,5002000)
    assert result['expiry']==1510000 and result['expired_guard']==-11
    assert result['calls_before_guard']==(2 if enabled else 1)
    assert result['reason']==0
    if enabled:
        assert result['logged_t4']==1010000 and result['logged_decision']==1045000
        assert result['last_phase']==5
    else:assert result['log_calls']==result['clock_quota']==0


@pytest.mark.parametrize('scenario,expected',[
    ('observed-stable',0),('observed-disconnect',-11),
    ('observed-control-busy',-11),('observed-control-error',-11),
    ('observed-new-gateway',-11),('observed-wrong-sender',-11),
])
def test_actual_context_preserves_only_same_observed_gateway_until_original_expiry(native,scenario,expected):
    enabled,run=native;result=run(scenario)
    assert result['rc']==0 and result['accepted']==1
    assert result['context_guard']==expected
    assert result['expiry']==1510000 and result['expired_guard']==-11
    assert (result['lower'],result['upper'])==(4993000,5002000)


@pytest.mark.parametrize('scenario,expected,reason,phase',[
    ('entry-failure',-71,8,6),('context-failure',-11,0,7),
    ('mutex-busy',-16,0,3),('context-change',-18,0,4),
])
def test_existing_failure_precedence_and_context_guards_are_preserved(native,scenario,expected,reason,phase):
    enabled,run=native;result=run(scenario)
    assert result['rc']==expected and result['reason']==reason and result['accepted']==0
    if enabled:assert result['last_phase']==phase


def test_invalid_wire_preserves_protocol_error_even_when_entry_clock_failed(native):
    enabled,run=native;result=run('entry-failure',wire=b'invalid')
    assert result['rc']==-71 and result['reason']==0
    assert result['log_calls']==0


def test_nonclock_reply_keeps_its_existing_current_clock_guard_when_entry_read_fails(native):
    enabled,run=native;original=reply();value=schema.DriveLink(major=2)
    value.accepted.host_boot=original.clock.host_boot;value.accepted.gateway_instance=original.clock.gateway_instance
    state=value.accepted.state
    state.CopyFrom(schema.DriveState(major=2,source_vm=original.clock.probe.vm,
        source_execution=original.clock.probe.execution,source_app_instance=original.clock.probe.app_instance,
        source_generation=1,state_revision=1,frame_epoch=original.clock.vehicle.odom_epoch,
        odom_epoch=original.clock.vehicle.odom_epoch,configuration='lattice-lab-1',primitive='a'*64,
        reference_id='road:test',reference_revision=1,path_revision=1,maneuver_revision=1,direction=0,
        maneuver_phase=0,merge_goal=schema.Pose(x=1,y=1,yaw=0),vehicle_sequence=1,calculation_sequence=1,
        tracker_index=0,complete=True,message_id='88888888-8888-4888-8888-888888888888',
        calibration='encoder-lab-1',map_epoch=original.clock.vehicle.odom_epoch,observation_seq=1))
    for x in (0.,1.):state.path.add(pose=schema.Pose(x=x,y=1,yaw=0),steering_rad=0,velocity_mps=.1)
    complete_state2_metadata(state)
    result=run('nonclock-entry-failure',value=value)
    assert result['rc']==0 and result['reason']==0 and result['accepted']==1
    assert result['expiry']==1510000 and result['expired_guard']==-11
    assert result['log_calls']==0 and result['calls_before_guard']==2


def test_true_network_uncertainty_and_identity_difference_still_reject(native):
    enabled,run=native;result=run('network-slow')
    assert result['rc']==-62 and result['accepted']==0
    if enabled:assert result['last_phase']==2
    value=reply();value.clock.probe.sequence=2
    result=run(value=value)
    assert result['rc']==-71 and result['accepted']==0
    if enabled:assert result['last_phase']==1


def test_caps_are_spent_when_log_backend_drops_or_filter_does_not_evaluate_arguments(native):
    enabled,run=native
    for scenario in ('cap','drop','filtered'):
        result=run(scenario)
        assert result['rc']==0 and result['accepted']==50 and result['reason']==0
        assert result['expiry']==1510000 and result['expired_guard']==-11
        assert result['clock_quota']==(16 if enabled else 0)
        assert result['map_quota']==(8 if enabled else 0)
        # 50 entry reads + 20 map current-time guards + at most16 diagnostic reads.
        assert result['calls_before_guard']==(86 if enabled else 70)
        assert result['log_calls']==(24 if enabled and scenario!='filtered' else 0)
        assert result['emitted']==(24 if enabled and scenario=='cap' else 0)


def test_diagnostic_clock_failure_does_not_change_success_or_original_reason(native):
    enabled,run=native;result=run('diag-failure')
    assert result['rc']==0 and result['expiry']==1510000
    # With diagnostics off, call2 belongs to the subsequent physical guard.
    assert result['reason']==(0 if enabled else 8)
    assert result['expired_guard']==(-5 if not enabled else -11)


@pytest.mark.parametrize('scenario,expected,reason,phase,actual',[
    ('map-clock-failure',-11,0,1,-11),('map-lock-busy',-11,0,1,-16),
    ('map-source-busy',-16,0,2,1),('map-world-failure',-71,10,3,10),
])
def test_map_phase_preserves_original_return_and_world_reason(native,scenario,expected,reason,phase,actual):
    enabled,run=native;result=run(scenario)
    assert result['rc']==expected and result['reason']==reason
    if enabled:
        assert result['map_rows']==1 and result['last_map_phase']==phase
        assert result['map_return']==expected and result['map_actual']==actual


def test_diagnostic_kconfig_defaults_off_and_requires_the_existing_nonblocking_deferred_logger():
    text=(HOME/'zephyr/Kconfig').read_text()
    block=text.split('config XENOVA_DRIVE_INPUT_DIAGNOSTICS',1)[1]
    assert 'default n' in block
    assert 'depends on XENOVA_DRIVE_LATTICE && LOG && LOG_MODE_DEFERRED' in block
    assert 'depends on !LOG_BLOCK_IN_THREAD && !LOG_MODE_IMMEDIATE && !LOG_FRONTEND' in block
    assert 'select ' not in block and 'NATIVE_TEST' not in block
