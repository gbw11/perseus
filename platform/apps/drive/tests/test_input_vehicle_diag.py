"""Actual C/nanopb vehicle guards and finite optional input diagnostics.

RTOS clocks/context are explicit fixtures. Ring add/reset are real except
specific lower-layer busy/stale fault hooks. A separate actual engine test
distinguishes writer reservation from decoded/published map acceptance.
"""
import ctypes as C
import json
import subprocess

import pytest

from test_input_clock import HOME, HARNESS, RUNTIME, reply, schema, encode
from test_engine import library, fixture, vehicle as engine_vehicle, send_vehicle, load


PREFIX = HARNESS.split('void test_log(const char *fmt,...){',1)[0]
PREFIX = PREFIX.replace('static unsigned clock_calls,clock_fail_call;',
                        'static unsigned clock_calls,clock_fail_call,context_calls;')
PREFIX = PREFIX.replace(' fake_now+=10000;if(context_rc)',
                        ' context_calls++;fake_now+=10000;if(context_rc)')
PREFIX = PREFIX.replace('.clock_error_us=20000,.clock_lease_us=500000};',
                        '.clock_error_us=20000,.clock_lease_us=500000,.pose_age_us=200000};')
PREFIX = PREFIX.replace('static unsigned lock_depth,log_calls,emitted,clock_rows,map_rows,last_phase,last_map_phase;',
                        'static unsigned lock_depth,log_calls,emitted;')
PREFIX = PREFIX.replace('static int map_actual,map_return;','')
PREFIX = PREFIX.replace('static uint64_t logged_t4,logged_decision;','')
PREFIX = PREFIX.replace('strcpy(out->auto_instance_id,instance);return 0;',
                        'strcpy(out->auto_instance_id,instance);strcpy(out->world_profile,lp_selected_config.id);'
                        'strcpy(out->world_broker_ipv4,"192.0.2.1");out->world_port=1883;return 0;')

BODY = r'''
static unsigned vehicle_rows,vehicle_phase,sv,nv,raw_error,receive_rows,receive_phase;
static int vehicle_rc,vehicle_ret;
static int receive_rc,receive_world;
static unsigned receive_before,receive_after,poll_calls,receive_mode;
static uint64_t observed_seq,observed_pi,observed_now;
static unsigned add_calls,reset_calls;
static bool reset_busy,second_add_fail;
enum dw_result __real_dw_odom_add(struct dw_odometry *,const dw_vehicle *);
enum dw_result __real_dw_odom_reset(struct dw_odometry *);
enum dw_result __wrap_dw_odom_add(struct dw_odometry *owner,const dw_vehicle *sample){
 add_calls++;if(second_add_fail&&add_calls==2)return DW_STALE;
 return __real_dw_odom_add(owner,sample);
}
enum dw_result __wrap_dw_odom_reset(struct dw_odometry *owner){
 reset_calls++;return reset_busy?DW_BUSY:__real_dw_odom_reset(owner);
}
void test_log(const char *fmt,...){
 assert(!lock_depth);log_calls++;if(!drop_logs)emitted++;
 va_list args;va_start(args,fmt);
 if(!strncmp(fmt,"XD_VEHICLE_DIAG",15)){
  vehicle_rows++;(void)va_arg(args,unsigned);vehicle_phase=va_arg(args,unsigned);
  vehicle_rc=va_arg(args,int);vehicle_ret=va_arg(args,int);sv=va_arg(args,unsigned);nv=va_arg(args,unsigned);
  observed_seq=va_arg(args,unsigned long long);observed_pi=va_arg(args,unsigned long long);
  raw_error=va_arg(args,unsigned);observed_now=va_arg(args,unsigned long long);
 }else if(!strncmp(fmt,"XD_RECEIVE_DIAG",15)){
  receive_rows++;(void)va_arg(args,unsigned);receive_phase=va_arg(args,unsigned);
  receive_rc=va_arg(args,int);receive_world=va_arg(args,int);
  receive_before=va_arg(args,unsigned);receive_after=va_arg(args,unsigned);
 }else assert(0);
 va_end(args);
}
/* Actual maps() owner callsite; only the engine result and external RTOS/MQTT
 * are fixtures here. The actual engine behavior is tested separately below. */
bool xd_stopping(void){return false;}
void k_sleep(int64_t delay){(void)delay;atomic_set(&stop,1);}
void xm_zclient_close(struct xm_zclient *client){(void)client;}
int xm_zclient_init(struct xm_zclient *client,const char *vm,const char *app,const char *name,const struct xm_zconfig *cfg){
 (void)client;(void)vm;(void)app;(void)name;(void)cfg;assert(0);return -EIO;
}
int xm_zclient_subscribe(struct xm_zclient *client,const char *topic,int qos){(void)client;(void)topic;(void)qos;assert(0);return -EIO;}
int xm_zclient_poll(struct xm_zclient *client,int wait){(void)client;(void)wait;return 0;}
enum de_result de_receive_drain(struct drive_engine *e){(void)e;return DE_OK;}
enum de_result de_receive_poll(struct drive_engine *e,uint64_t now){
 (void)now;poll_calls++;
 if(receive_mode==2){e->receive_awaiting=false;e->maps.published++;return DE_OK;}
 if(receive_mode==3){e->receive_awaiting=false;return DE_EXPIRED;}
 return receive_mode==1?DE_POSE:receive_mode==4?DE_BUSY:DE_OK;
}
int main(int argc,char **argv){
 assert(argc==3);uint8_t wire[DW_WIRE_MAX];FILE *f=fopen(argv[1],"rb");assert(f);
 size_t length=fread(wire,1,sizeof(wire),f);fclose(f);const char *scenario=argv[2];
 engine=&fixture_engine;dw_pool_init(&engine->maps);dw_odom_init(&engine->odometry);
 context_available=clock_available=true;strcpy(gateway,gateway_id);
 current_clock.lower=4992000;current_clock.upper=5002000;current_clock.expires_guest=50000000;
 strcpy(current_clock.gateway,gateway_id);strcpy(current_clock.trust.vehicle,"rc-test-1");
 strcpy(current_clock.trust.odom_epoch,"77777777-7777-4777-8777-777777777777");
 strcpy(current_clock.trust.calibration,"encoder-lab-1");
 if(!strcmp(scenario,"clock")||!strcmp(scenario,"decode-and-clock")||!strcmp(scenario,"clock-and-context"))clock_available=false;
 if(!strcmp(scenario,"context")||!strcmp(scenario,"clock-and-context"))context_rc=-EAGAIN;
 if(!strcmp(scenario,"clock-read-error"))clock_fail_call=1;
 if(!strcmp(scenario,"clock-busy"))mutex_rc=-EBUSY;
 if(!strcmp(scenario,"order"))received_vehicle=7000000;
 if(!strcmp(scenario,"overflow"))current_clock.lower=current_clock.upper-(int64_t)UINT32_MAX;
 if(!strcmp(scenario,"odom-busy"))atomic_flag_test_and_set(&engine->odometry.lock);
 if(!strncmp(scenario,"reset-",6)){
  dw_vehicle old;assert(!dw_vehicle_decode(wire,length,&old));
  strcpy(old.odom_epoch,"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa");
  assert(!__real_dw_odom_add(&engine->odometry,&old));
  reset_busy=!strcmp(scenario,"reset-busy");second_add_fail=!strcmp(scenario,"reset-add-fail");
 }
 int rc;
 if(!strncmp(scenario,"receive-",8)){
  assert(!xd_context_get(&map_boot)); /* This receive-owner case requires a fresh setup. */
  receive_mode=!strcmp(scenario,"receive-wait")?1:!strcmp(scenario,"receive-publish")?2:
   !strcmp(scenario,"receive-drain")?3:!strcmp(scenario,"receive-abort-pending")?4:0;
  engine->receive_awaiting=receive_mode>0&&receive_mode<4;engine->receive_pending=receive_mode==4;
  atomic_store(&engine->last_world_result,DW_TIME);maps(NULL,NULL,NULL);rc=0;
 }else if(!strcmp(scenario,"cap")||!strcmp(scenario,"filtered")||!strcmp(scenario,"drop")){
  test_log_filtered=!strcmp(scenario,"filtered");drop_logs=!strcmp(scenario,"drop");
  clock_available=false;
  for(unsigned k=0;k<100;k++){fake_now=1010000;assert(vehicle_received(NULL,wire,length,INT64_MAX)==-EPROTO);}
  clock_available=true;fake_now=1010000;assert(!vehicle_received(NULL,wire,length,INT64_MAX));
  for(unsigned k=0;k<100;k++){fake_now=1010000;assert(vehicle_received(NULL,wire,length,INT64_MAX)==-EPROTO);}
  rc=0;
 }else if(!strcmp(scenario,"distinct-cap")){
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
  for(unsigned phase=1;phase<=17;phase++)for(unsigned k=0;k<20;k++)
   vehicle_diagnostic(phase,-EPROTO,-EPROTO,NULL,false,0,0);
  for(unsigned phase=1;phase<=4;phase++)for(unsigned k=0;k<20;k++)
   receive_diagnostic(phase,DE_POSE,2,2,6012000);
#endif
  rc=0;
 }else if(!strcmp(scenario,"saturated")){
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
  vehicle_phase_count[2]=UINT64_MAX;
#endif
  clock_available=false;rc=vehicle_received(NULL,wire,length,INT64_MAX);
 }else rc=vehicle_received(NULL,wire,length,INT64_MAX);
 unsigned quota=0,rquota=0;uint64_t p2=0,p10=0,p17=0;
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
 quota=vehicle_diag_count;rquota=receive_diag_count;p2=vehicle_phase_count[2];p10=vehicle_phase_count[10];p17=vehicle_phase_count[17];
#endif
 printf("{\"rc\":%d,\"phase\":%u,\"detail_rc\":%d,\"detail_ret\":%d,\"sv\":%u,\"nv\":%u,"
  "\"seq\":%llu,\"pi\":%llu,\"err\":%u,\"now\":%llu,\"clock_calls\":%u,\"context_calls\":%u,"
  "\"rows\":%u,\"quota\":%u,\"rrows\":%u,\"rquota\":%u,\"rphase\":%u,\"log_calls\":%u,\"emitted\":%u,"
  "\"p2\":%llu,\"p10\":%llu,\"p17\":%llu,\"odom_count\":%u,\"odom_peak\":%u,\"add_calls\":%u,\"reset_calls\":%u,\"stored_error\":%u,"
  "\"poll_calls\":%u,\"receive_rc\":%d,\"receive_world\":%d,\"receive_before\":%u,\"receive_after\":%u}\n",
  rc,vehicle_phase,vehicle_rc,vehicle_ret,sv,nv,(unsigned long long)observed_seq,(unsigned long long)observed_pi,
  raw_error,(unsigned long long)observed_now,clock_calls,context_calls,vehicle_rows,quota,receive_rows,rquota,receive_phase,log_calls,emitted,
  (unsigned long long)p2,(unsigned long long)p10,(unsigned long long)p17,(unsigned)engine->odometry.count,(unsigned)engine->odometry.high_water,
  add_calls,reset_calls,engine->odometry.count?engine->odometry.samples[0].clock_error_us:0,
  poll_calls,receive_rc,receive_world,receive_before,receive_after);
 return 0;
}
'''


def sample():
    value=schema.VehicleSample();value.CopyFrom(reply().clock.vehicle)
    value.pi_sample_us=6000000;value.gateway_instance='55555555-5555-4555-8555-555555555555'
    return value


@pytest.fixture(scope='module',params=[False,True],ids=['diagnostics-off','diagnostics-on'])
def vehicle_native(request,tmp_path_factory):
    directory=tmp_path_factory.mktemp('vehicle-input-diag');include=directory/'include'
    for name in ('xenova','zephyr/sys','zephyr/logging'):(include/name).mkdir(parents=True,exist_ok=True)
    (include/'xenova/drive_runtime.h').write_text(RUNTIME)
    (include/'xenova/drive_context.h').write_text('#include <xenova/drive_runtime.h>\n')
    (include/'zephyr/sys/atomic.h').write_text('#include <xenova/drive_runtime.h>\n')
    (include/'zephyr/logging/log.h').write_text('#define LOG_LEVEL_INF 3\n#define LOG_MODULE_REGISTER(...)\n#define LOG_INF(...) do { if(!test_log_filtered) test_log(__VA_ARGS__); } while(0)\n')
    source=directory/'vehicle.c';source.write_text(PREFIX+BODY);mqtt=HOME.parent/'common/xenova-mqtt-c';world=HOME/'world';binary=directory/'vehicle'
    args=['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-ffunction-sections','-fdata-sections',
          '-Wl,--gc-sections','-Wl,--wrap=dw_link_decode','-Wl,--wrap=dw_odom_add','-Wl,--wrap=dw_odom_reset','-fsanitize=undefined',
          *(['-DCONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS=1'] if request.param else []),
          *['-I'+str(p) for p in (include,HOME/'zephyr/src',HOME/'planner',world,world/'generated',world/'nanopb',mqtt/'include',mqtt/'third_party/jsmn')],
          str(source),str(world/'world.c'),str(world/'generated/world.pb.c'),*[str(p) for p in sorted((mqtt/'src').glob('*.c'))],
          *[str(world/'nanopb'/name) for name in ('pb_common.c','pb_decode.c','pb_encode.c')],'-lm','-o',str(binary)]
    built=subprocess.run(args,capture_output=True,text=True)
    assert built.returncode==0,built.stderr
    def run(scenario='normal',value=None,wire=None):
        path=directory/'input.bin';path.write_bytes(wire if wire is not None else encode(value if value is not None else sample()))
        ran=subprocess.run([str(binary),str(path),scenario],capture_output=True,text=True)
        assert ran.returncode==0,ran.stderr
        assert not ran.stderr
        return json.loads(ran.stdout)
    return request.param,run


@pytest.mark.parametrize('scenario,phase,public,detail,clocks,contexts',[
    ('clock',2,-71,-11,1,0),('clock-read-error',2,-71,-5,1,0),('clock-busy',2,-71,-16,1,0),
    ('context',3,-71,-11,1,1),('odom-busy',14,-11,1,1,1),
    ('reset-busy',15,-11,1,1,1),('reset-add-fail',16,-11,10,1,1),
    ('overflow',13,-34,-34,1,1),
])
def test_original_clock_context_and_odom_failure_guards(vehicle_native,scenario,phase,public,detail,clocks,contexts):
    enabled,run=vehicle_native;value=run(scenario)
    assert (value['rc'],value['clock_calls'],value['context_calls'])==(public,clocks,contexts)
    if enabled:
        assert value['phase']==phase and value['detail_ret']==public and value['detail_rc']==detail
        assert value['sv']==1 and value['nv']==(phase!=2)
    else:assert value['log_calls']==value['quota']==0


@pytest.mark.parametrize('name,field,replacement,phase',[
    ('host','host_boot','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',4),
    ('gateway','gateway_instance','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',6),
    ('vehicle','vehicle','different-vehicle',7),
    ('epoch','odom_epoch','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',8),
    ('calibration','calibration','different-calibration',9),
    ('future','pi_sample_us',7000000,11),('expired','pi_sample_us',5000000,12),
])
def test_actual_decoder_then_original_identity_order_and_age_guards(vehicle_native,name,field,replacement,phase):
    enabled,run=vehicle_native;body=sample();setattr(body,field,replacement);value=run(value=body)
    assert value['rc']==-71 and value['odom_count']==0 and value['clock_calls']==value['context_calls']==1
    if enabled:assert value['phase']==phase and value['sv']==value['nv']==1


def test_precedence_unknown_fields_and_normal_epoch_reset_recovery(vehicle_native):
    enabled,run=vehicle_native
    invalid=run('decode-and-clock',wire=b'invalid')
    assert invalid['rc']==-71 and invalid['clock_calls']==invalid['context_calls']==0
    if enabled:assert invalid['phase']==1 and invalid['sv']==invalid['nv']==invalid['seq']==invalid['err']==0
    clock=run('clock-and-context');assert clock['rc']==-71 and clock['context_calls']==0
    body=sample();body.host_boot='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';body.ClearField('gateway_instance')
    if enabled:assert run(value=body)['phase']==4
    body=sample();body.ClearField('gateway_instance');missing=run(value=body)
    assert missing['rc']==-71
    if enabled:assert missing['phase']==5
    ordered=sample();ordered.pi_sample_us=7000000;value=run('order',value=ordered)
    assert value['rc']==-71
    if enabled:assert value['phase']==10
    for scenario in ('normal','reset-normal'):
        value=run(scenario)
        assert value['rc']==0 and value['odom_count']==value['odom_peak']==1
        assert value['stored_error']==10500 and value['reset_calls']==(scenario=='reset-normal')
        if enabled:assert value['phase']==17 and value['err']==500 and value['seq']==1 and value['now']==6012000
    bad=sample();bad.quality=0;value=run(wire=bad.SerializeToString(deterministic=True))
    assert value['rc']==-71 and value['clock_calls']==value['context_calls']==0
    if enabled:assert value['phase']==1 and value['sv']==0
    for suffix in (b'\x30\x02',b'\x32\x01\x01'):
        value=run(wire=encode(sample())+suffix)
        assert value['rc']==-71 and value['clock_calls']==value['context_calls']==0
        if enabled:assert value['phase']==1 and value['sv']==0
    assert run(wire=encode(sample())+b'\xf8\x03\x01')['rc']==0  # supported unknown optional field


def test_distinct_reason_quotas_counters_filter_drop_and_saturation(vehicle_native):
    enabled,run=vehicle_native
    for mode in ('cap','drop','filtered'):
        value=run(mode)
        assert value['rc']==0 and value['odom_count']==1
        assert (value['clock_calls'],value['context_calls'])==(201,101)
        if enabled:
            assert (value['quota'],value['p2'],value['p10'],value['p17'])==(3,100,100,1)
            assert value['log_calls']==(0 if mode=='filtered' else 3)
            assert value['emitted']==(3 if mode=='cap' else 0)
        else:assert value['quota']==value['log_calls']==0
    value=run('distinct-cap')
    assert value['quota']==(16 if enabled else 0) and value['rquota']==(8 if enabled else 0)
    assert value['rows']==(16 if enabled else 0) and value['rrows']==(8 if enabled else 0)
    value=run('saturated')
    assert value['p2']==(2**64-1 if enabled else 0) and value['quota']==0


@pytest.mark.parametrize('scenario,phase,rc,before,after',[
    ('receive-idle',0,0,0,0),('receive-wait',1,5,2,2),
    ('receive-publish',2,0,2,0),('receive-drain',3,7,2,0),('receive-abort-pending',4,1,1,1),
])
def test_actual_map_owner_reports_only_existing_pending_result(vehicle_native,scenario,phase,rc,before,after):
    enabled,run=vehicle_native;value=run(scenario)
    assert value['poll_calls']==1 and value['clock_calls']==1
    if enabled and phase:
        assert value['rphase']==phase and value['receive_rc']==rc and value['receive_world']==8
        assert (value['receive_before'],value['receive_after'])==(before,after)
        assert value['rquota']==value['rrows']==1
    else:assert value['rquota']==value['rrows']==value['log_calls']==0


def test_real_engine_rejected_map_reservation_peak_is_not_decode_or_publish(library):
    library.engine_memory.argtypes=[C.c_void_p,C.c_uint,C.POINTER(C.c_uint64)]
    engine=library.engine_new();memory=(C.c_uint64*6)()
    try:
        assert library.engine_world(engine,b'invalid',7,1200000)!=0
        assert library.engine_memory(engine,0,memory)==0
        assert memory[2]==0 and memory[3]>0  # aborted writer, no adopted map
        first_peak=memory[3]
        world=fixture.world();load(library,engine,world)
        assert library.engine_memory(engine,0,memory)==0
        assert memory[2]>0 and memory[3]==first_peak  # real decode, exact anchor still missing
        assert library.engine_plan(engine,1200000,4096)==5
        send_vehicle(library,engine,engine_vehicle());send_vehicle(library,engine,engine_vehicle(2,at=1220000))
        assert library.engine_plan(engine,1220000,4096)==0
        assert library.engine_memory(engine,0,memory)==0 and memory[2]>0
        assert library.engine_memory(engine,1,memory)==0 and memory[3]>0
    finally:library.engine_free(engine)
