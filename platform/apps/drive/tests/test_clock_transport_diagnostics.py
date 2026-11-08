"""Actual input C/codec/command, with explicit RTOS/socket/clock fault fixtures.

No planner/vehicle acceptance is substituted here: these tests cover only the
new diagnostic ownership and original transport calls/results. Existing input
guards remain covered by test_input_clock/vehicle/transport_barrier.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_input_clock import HOME, RUNTIME, reply
from xenova_drive_world import encode


HARNESS=r'''
#define _POSIX_C_SOURCE 200809L
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <pthread.h>
#include <time.h>
#include <xenova/drive_runtime.h>
#include "engine.h"
#define XENOVA_LATTICE_APP_H
int drive_lattice_snapshot(dw_state *);
void test_log(const char *,...);
#include INPUT_SOURCE
static uint64_t now_us=1000000;
static unsigned reads,fail_read,encode_calls,publish_calls,poll_calls,init_calls,subscribe_calls,closes;
static int publish_rc,poll_rc,context_rc,encode_fail;
static bool delivering,deliver;
#if defined(CONFIG_SHELL)
static bool check_shell_released;
#endif
static uint8_t reply_wire[DW_WIRE_MAX];static size_t reply_bytes;
static xenova_drive_DriveLink transmitted;
const struct lp_config lp_selected_config={.id="lattice-lab-1",.vehicle="rc-test-1",.calibration="encoder-lab-1",.clock_error_us=20000,.clock_lease_us=1000000};
const struct lp_assets lp_selected_assets={.sha256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"};
int xdiag_clock_us(uint64_t *out){reads++;if(reads==fail_read)return -EIO;*out=now_us;return 0;}
int xd_context_get(struct xt_bootstrap *out){
 if(context_rc)return context_rc;memset(out,0,sizeof(*out));
 strcpy(out->identity.vm_id,"11111111-1111-4111-8111-111111111111");
 strcpy(out->identity.execution_id,"22222222-2222-4222-8222-222222222222");
 strcpy(out->identity.host_boot_id,"44444444-4444-4444-8444-444444444444");
 strcpy(out->auto_instance_id,"33333333-3333-4333-8333-333333333333");
 if(delivering)now_us+=10000;return 0;
}
int k_mutex_lock(struct k_mutex *m,int t){(void)m;(void)t;return 0;}
void k_mutex_unlock(struct k_mutex *m){(void)m;}
int64_t k_uptime_get(void){return (int64_t)(now_us/1000);}
bool xd_stopping(void){return false;}
int xd_io_binary(const struct xm_zbinary *b){(void)b;return 0;}
void xm_zclient_close(struct xm_zclient *c){closes++;c->socket_open=c->connected=false;}
int xm_zclient_init(struct xm_zclient *c,const char *vm,const char *a,const char *b,const struct xm_zconfig *cfg){
 (void)vm;(void)a;(void)b;(void)cfg;init_calls++;c->socket_open=c->connected=true;return 0;
}
int xm_zclient_subscribe(struct xm_zclient *c,const char *t,int q){(void)c;(void)t;(void)q;subscribe_calls++;return 0;}
int xm_zclient_poll(struct xm_zclient *c,int t){
 (void)c;assert(t==0);poll_calls++;now_us+=3000;
 if(deliver){delivering=true;int rc=link_received(NULL,reply_wire,reply_bytes,k_uptime_get());assert(rc==0||rc==-EPROTO||rc==-ETIME);delivering=false;deliver=false;}
 return poll_rc;
}
int mqtt_publish(struct mqtt_client *c,const struct mqtt_publish_param *p){
 (void)c;publish_calls++;assert(dw_link_decode(p->message.payload.data,p->message.payload.len,&transmitted)==DW_OK);now_us+=3000;return publish_rc;
}
struct k_thread *k_thread_create(struct k_thread *t,void *s,size_t n,void (*f)(void *,void *,void *),void *a,void *b,void *c,int p,int o,int d){
 (void)s;(void)n;(void)f;(void)a;(void)b;(void)c;(void)p;(void)o;(void)d;return t;
}
int k_thread_join(struct k_thread *t,int64_t d){(void)t;(void)d;return 0;}
void k_thread_name_set(struct k_thread *t,const char *n){(void)t;(void)n;}
void k_sleep(int64_t t){now_us+=(uint64_t)t*1000;}
int drive_lattice_snapshot(dw_state *out){(void)out;return -EAGAIN;}
enum de_result de_world(struct drive_engine *e,const uint8_t *b,size_t n,uint64_t u){(void)e;(void)b;(void)n;(void)u;return DE_OK;}
void test_log(const char *fmt,...){(void)fmt;}
bool __real_pb_encode(pb_ostream_t *,const pb_msgdesc_t *,const void *);
bool __wrap_pb_encode(pb_ostream_t *s,const pb_msgdesc_t *fields,const void *value){
 if(fields==xenova_drive_DriveLink_fields){encode_calls++;now_us+=1000;if(encode_fail)return false;}
 return __real_pb_encode(s,fields,value);
}
enum dw_result __real_dw_link_decode(const uint8_t *,size_t,xenova_drive_DriveLink *);
enum dw_result __wrap_dw_link_decode(const uint8_t *b,size_t n,xenova_drive_DriveLink *out){
 enum dw_result rc=__real_dw_link_decode(b,n,out);if(delivering)now_us+=25000;return rc;
}
#if defined(CONFIG_SHELL)
void shell_print(const struct shell *sh,const char *fmt,...){
 (void)sh;
 if(check_shell_released){
  for(unsigned index=0;index<8;index++)assert(atomic_load(&ct_slots[index].state)==2);
  struct ct_row row={0};assert(ct_store(0,&row));
 }
 va_list args;va_start(args,fmt);vprintf(fmt,args);printf("\n");va_end(args);
}
#endif
static void probe_value(xenova_drive_DriveLink *out,uint64_t sequence){
 memset(out,0,sizeof(*out));out->major=2;out->which_payload=xenova_drive_DriveLink_probe_tag;
 strcpy(out->payload.probe.vm,"11111111-1111-4111-8111-111111111111");
 strcpy(out->payload.probe.execution,"22222222-2222-4222-8222-222222222222");
 strcpy(out->payload.probe.app_instance,"33333333-3333-4333-8333-333333333333");
 out->payload.probe.sequence=sequence;out->payload.probe.guest_send_us=1000000;probe_sequence=sequence;probe_sent=1000000;
}
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
static atomic_bool writer_done;
static void *write_rows(void *unused){
 (void)unused;for(unsigned k=1;k<=10000;k++){struct ct_row r={0};r.sequence=k;r.g0=k*7;r.eb=k*11;r.known=CT_ACTIVE|CT_EB;(void)ct_store(0,&r);}
 atomic_store(&writer_done,true);return NULL;
}
static void concurrency(void){
 pthread_t writer;assert(!pthread_create(&writer,NULL,write_rows,NULL));unsigned sampled=0;
 while(!atomic_load(&writer_done)){struct ct_row r;int rc=ct_read(0,&r);assert(rc==0||rc==-EBUSY||rc==-ENODATA);if(!rc){assert(r.g0==r.sequence*7&&r.eb==r.sequence*11);sampled++;}}
 assert(!pthread_join(writer,NULL));struct ct_row r;assert(ct_read(0,&r)==0);assert(r.g0==r.sequence*7&&r.eb==r.sequence*11);(void)sampled;
}
static void synthetic_replies(void){
 for(unsigned k=1;k<=40;k++){
  struct ct_row row={0};row.sequence=k;row.g0=k;row.known=CT_ACTIVE|CT_WIDTH;row.width=k==17?90000:k==28?80000:(int64_t)k;row.match=3;row.event=1;ct_publish(&row);
 }
 struct ct_row a,b;assert(!ct_read(0,&a)&&!ct_read(1,&b));assert(a.sequence==1&&b.sequence==2);
 assert(!ct_read(6,&a)&&!ct_read(7,&b));assert((a.sequence==17&&b.sequence==28)||(a.sequence==28&&b.sequence==17));
 for(unsigned k=0;k<100;k++){
  xenova_drive_DriveLink p;probe_value(&p,100+k);struct ct_tx tx;ct_binary_begin(&p,&tx);
 }
 /* The initial real publish is also retired unanswered by the first loop. */
 assert(atomic_load(&ct_counts[CT_NO_REPLY])==100);
 assert(!ct_read(2,&a));assert(a.sequence>=37&&a.sequence<=40);
}
static void late_replies(void){
 xenova_drive_DriveLink response;assert(dw_link_decode(reply_wire,reply_bytes,&response)==DW_OK);
 for(unsigned k=1;k<=40;k++){
  xenova_drive_DriveLink probe;probe_value(&probe,k);now_us=1000000;
  assert(binary_publish(&link_client,"fixture/out",&probe)==0);
  response.payload.clock.probe.sequence=k;
  pb_ostream_t stream=pb_ostream_from_buffer(reply_wire,sizeof(reply_wire));
  assert(__real_pb_encode(&stream,xenova_drive_DriveLink_fields,&response));reply_bytes=stream.bytes_written;
  now_us=1010000;deliver=true;assert(LINK_POLL(&link_client)==0);
 }
 assert(clock_diag_count==16&&atomic_load(&ct_counts[CT_RECORDS])==40);
 for(unsigned index=2;index<6;index++){struct ct_row row;assert(!ct_read(index,&row));assert(row.sequence>=37&&row.sequence<=40&&row.phase==5&&row.match==3);}
}
#endif
int main(int argc,char **argv){
 assert(argc==3);FILE *f=fopen(argv[1],"rb");assert(f);reply_bytes=fread(reply_wire,1,sizeof(reply_wire),f);fclose(f);
 const char *scenario=argv[2];xenova_drive_DriveLink p;probe_value(&p,1);
 context_available=true;strcpy(gateway,"55555555-5555-4555-8555-555555555555");
 if(!strcmp(scenario,"encode-fail"))encode_fail=1;
 if(!strcmp(scenario,"wire-fail"))publish_rc=-EIO;
 if(!strcmp(scenario,"extra-clock-fail"))fail_read=1;
 int result=binary_publish(&link_client,"fixture/out",&p);
 assert(p.payload.probe.sequence==1&&p.payload.probe.guest_send_us==1000000&&probe_sent==1000000&&probe_sequence==1);
 assert(encode_calls==1&&publish_calls==(encode_fail?0U:1U));
 assert(link_client.socket_io.deadline_ms==(encode_fail?0:1251));
#if defined(CONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS)
 if(!strcmp(scenario,"encode-fail")){assert(result==-EMSGSIZE);assert(ct_active.encode_rc==-EMSGSIZE&&!(ct_active.known&CT_TX_RC));}
 if(!strcmp(scenario,"wire-fail")){assert(result==-EIO);assert(ct_active.tx_rc==-EIO&&(ct_active.known&CT_TX_RC));}
 if(!strcmp(scenario,"extra-clock-fail")){assert(result==0&&atomic_get(&reason)==0);assert(!(ct_active.known&CT_EB)&&ct_active.diag_rc==-EIO);}
 if(!strcmp(scenario,"normal")||!strcmp(scenario,"unmatched")||!strcmp(scenario,"invalid-identity")||!strcmp(scenario,"poll-error")){
  if(!strcmp(scenario,"unmatched")||!strcmp(scenario,"invalid-identity")){assert(dw_link_decode(reply_wire,reply_bytes,&incoming)==DW_OK);if(!strcmp(scenario,"unmatched"))incoming.payload.clock.probe.sequence=2;else strcpy(incoming.payload.clock.probe.vm,"UNTRUSTED-INCOMING-TEXT");pb_ostream_t stream=pb_ostream_from_buffer(reply_wire,sizeof(reply_wire));assert(__real_pb_encode(&stream,xenova_drive_DriveLink_fields,&incoming));reply_bytes=stream.bytes_written;}
  now_us=1010000;deliver=true;if(!strcmp(scenario,"poll-error"))poll_rc=-EIO;
  assert(LINK_POLL(&link_client)==poll_rc);
  struct ct_row r;assert(!ct_read(0,&r));assert(r.reply_sequence==(!strcmp(scenario,"unmatched")?2U:1U));
  assert(r.g0==1000000&&r.g3==1013000&&r.poll_begin==1010000&&r.poll_end==1048000);
  if(!strcmp(scenario,"unmatched")){assert(r.match==0&&!(r.known&CT_WIDTH)&&!(r.known&CT_EB));assert(atomic_load(&ct_counts[CT_UNMATCHED])==1);}
  else if(!strcmp(scenario,"invalid-identity")){assert(r.match==1&&!(r.known&CT_WIDTH)&&r.phase==1&&r.reply_rc==-EPROTO);assert(!strcmp(r.vm,"11111111-1111-4111-8111-111111111111"));}
  else {assert(r.match==3&&r.width==12000&&r.phase==5&&r.known&CT_WIDTH);assert(r.poll_max==38000);assert(r.poll_rc==poll_rc);}
 }
 if(!strcmp(scenario,"setup")){bool unavailable;context_rc=-EAGAIN;assert(LINK_SETUP(&link_client,&link_boot,false,&unavailable)==-EAGAIN&&unavailable);assert(ct_active.setup_rc==-EAGAIN);context_rc=0;assert(LINK_SETUP(&link_client,&link_boot,false,&unavailable)==0);assert(init_calls==1&&subscribe_calls==1);}
 if(!strcmp(scenario,"retention"))synthetic_replies();
 if(!strcmp(scenario,"late-replies"))late_replies();
 if(!strcmp(scenario,"concurrent"))concurrency();
 if(!strcmp(scenario,"busy")){atomic_store(&ct_slots[0].state,3);assert(!ct_store(0,&ct_active));struct ct_row r;assert(ct_read(0,&r)==-EBUSY);assert(atomic_load(&ct_counts[CT_SKIPPED])==1);}
 if(!strcmp(scenario,"saturation")){atomic_store(&ct_counts[CT_RECORDS],UINT_MAX);struct ct_row r={0};ct_publish(&r);assert(atomic_load(&ct_counts[CT_RECORDS])==UINT_MAX);}
 if(!strcmp(scenario,"recovery")){publish_rc=-EIO;probe_value(&p,2);assert(binary_publish(&link_client,"fixture/out",&p)==-EIO);publish_rc=0;probe_value(&p,3);assert(binary_publish(&link_client,"fixture/out",&p)==0);assert(transmitted.payload.probe.sequence==3&&atomic_get(&reason)==0);}
 if(!strcmp(scenario,"other")){
  xenova_drive_DriveLink q=p;q.which_payload=xenova_drive_DriveLink_applied_tag;
  memset(&q.payload.applied,0,sizeof(q.payload.applied));xenova_drive_AppliedState *a=&q.payload.applied;
  strcpy(a->vm,p.payload.probe.vm);strcpy(a->execution,p.payload.probe.execution);strcpy(a->app_instance,p.payload.probe.app_instance);
  strcpy(a->host_boot,"44444444-4444-4444-8444-444444444444");strcpy(a->gateway_instance,gateway);
  strcpy(a->frame_epoch,"44444444-4444-4444-8444-444444444444");strcpy(a->odom_epoch,"77777777-7777-4777-8777-777777777777");
  strcpy(a->configuration,lp_selected_config.id);strcpy(a->calibration,lp_selected_config.calibration);
  strcpy(a->primitive,lp_selected_assets.sha256);strcpy(a->reference_id,"straight-lab-road");
  a->state_revision=1;a->reference_revision=1;a->path_revision=1;a->maneuver_revision=1;
  a->rebuilt=true;a->application_sequence=1;
  assert(binary_publish(&link_client,"fixture/out",&q)==0);
  assert(ct_active.other_calls==1&&ct_active.other_max==4000&&ct_active.other_kind==2);
 }
 if(!strcmp(scenario,"shell-released")){
  for(unsigned index=0;index<8;index++)assert(ct_store(index,&ct_active));
  check_shell_released=true;
 }
 if(!strcmp(scenario,"formatter-bound")){
  struct ct_row row=ct_active;
  row.record=row.sequence=row.g0=row.live_sequence=row.live_g0=row.reply_sequence=row.echo_g0=UINT64_MAX;
  row.eb=row.ee=row.tb=row.te=row.g3=row.p1=row.p2=row.poll_begin=row.poll_end=row.previous_poll_end=UINT64_MAX;
  row.setup_max=row.poll_max=row.gap_max=row.other_total=row.other_max=UINT64_MAX;row.width=INT64_MIN;
  row.known=262143;row.setup_calls=row.poll_calls=row.other_calls=row.phase=row.match=row.event=row.other_kind=UINT_MAX;
  row.encode_rc=row.tx_rc=row.reply_rc=row.clock_rc=row.setup_rc=row.poll_rc=row.diag_rc=INT_MIN;
  for(unsigned index=0;index<8;index++)assert(ct_store(index,&row));
  for(unsigned index=0;index<CT_COUNTERS;index++)atomic_store(&ct_counts[index],UINT_MAX);
  atomic_store(&ct_first_clock_rc,INT_MIN);
 }
 char *command[]={"xenova_clock"};assert(ct_shell(NULL,1,command)==0);assert(ct_shell(NULL,2,command)==-EINVAL);
 printf("RESULT {\"rc\":%d,\"encode_calls\":%u,\"publish_calls\":%u,\"poll_calls\":%u,\"reads\":%u,\"reason\":%ld,\"row_bytes\":%zu,\"static_bytes\":%zu}\n",result,encode_calls,publish_calls,poll_calls,reads,atomic_get(&reason),sizeof(struct ct_row),sizeof(ct_slots)+sizeof(ct_active)+sizeof(ct_pending)+sizeof(ct_counts)+sizeof(ct_first_clock_rc)+sizeof(ct_peak));
#else
 printf("RESULT {\"rc\":%d,\"encode_calls\":%u,\"publish_calls\":%u,\"poll_calls\":%u,\"reads\":%u,\"reason\":%ld}\n",result,encode_calls,publish_calls,poll_calls,reads,atomic_get(&reason));
#endif
 return 0;
}
'''


@pytest.fixture(scope='module')
def native(tmp_path_factory):
    directory=tmp_path_factory.mktemp('clock-transport-native');include=directory/'include'
    for name in ('xenova','zephyr/sys','zephyr/logging','zephyr/shell'):(include/name).mkdir(parents=True,exist_ok=True)
    (include/'xenova/drive_runtime.h').write_text(RUNTIME)
    for name in ('xenova/drive_context.h','zephyr/sys/atomic.h'):(include/name).write_text('#include <xenova/drive_runtime.h>\n')
    (include/'zephyr/logging/log.h').write_text('#define LOG_LEVEL_INF 3\n#define LOG_MODULE_REGISTER(...)\n#define LOG_INF(...) test_log(__VA_ARGS__)\n')
    (include/'zephyr/shell/shell.h').write_text('struct shell {int unused;};\nvoid shell_print(const struct shell *,const char *,...);\n#define SHELL_CMD_REGISTER(name,sub,help,fn) static void *test_shell_registration __attribute__((used))=(void *)&fn\n')
    source=directory/'input.c';source.write_text(HARNESS)
    mqtt=HOME.parent/'common/xenova-mqtt-c';world=HOME/'world';binaries={}
    common=['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-Wl,--wrap=pb_encode','-Wl,--wrap=dw_link_decode','-fsanitize=undefined','-fstack-usage','-DCONFIG_XENOVA_MQTT_BOUNDED_IO=1','-DCONFIG_XENOVA_MQTT_IO_MS=250',
        *['-I'+str(path) for path in (include,HOME/'zephyr/src',HOME/'planner',world,world/'generated',world/'nanopb',mqtt/'include',mqtt/'third_party/jsmn')],str(source),str(world/'world.c'),str(world/'generated/world.pb.c'),*[str(p) for p in sorted((mqtt/'src').glob('*.c'))],*[str(world/'nanopb'/name) for name in ('pb_common.c','pb_decode.c','pb_encode.c')],'-lm','-pthread']
    for enabled in (False,True):
        binary=directory/('on' if enabled else 'off')
        command=[*common,'-DINPUT_SOURCE="'+str(HOME/'zephyr/src/lattice_input.c')+'"',*(['-DCONFIG_XENOVA_DRIVE_INPUT_DIAGNOSTICS=1','-DCONFIG_SHELL=1'] if enabled else []),'-o',str(binary)]
        result=subprocess.run(command,capture_output=True,text=True,timeout=120)
        assert result.returncode==0,result.stderr
        binaries[enabled]=binary
    path=directory/'reply.bin';path.write_bytes(encode(reply()))
    def run(scenario,enabled=True):
        result=subprocess.run([str(binaries[enabled]),str(path),scenario],capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
        assert not result.stderr
        lines=result.stdout.splitlines()
        return json.loads(next(line[7:] for line in lines if line.startswith('RESULT '))),lines
    return run,directory,common,source


@pytest.mark.parametrize('scenario',['normal','encode-fail','wire-fail','extra-clock-fail','unmatched','invalid-identity','poll-error','setup','retention','late-replies','concurrent','busy','saturation','recovery','other','shell-released','formatter-bound'])
def test_actual_product_transport_and_bounded_command(native,scenario):
    run,*_=native;result,lines=run(scenario)
    assert result['reason']==0
    assert result['row_bytes']<=512 and result['static_bytes']<=6144
    meta=json.loads(next(line.split(' ',1)[1] for line in lines if line.startswith('XD_CLOCK_TRANSPORT_META ')))
    assert meta['schema']==1 and meta['slots']==8
    rows=[json.loads(line.split(' ',1)[1]) for line in lines if line.startswith('XD_CLOCK_TRANSPORT_ROW ')]
    assert len(rows)==8
    if scenario=='formatter-bound':
        # Deliberately use even out-of-contract enum maxima: this is the
        # formatter's byte bound, not a valid parser/clock-acceptance fixture.
        raw='\r\n'.join(line for line in lines if line.startswith('XD_CLOCK_TRANSPORT'))+'\r\n'
        assert len(raw.encode('ascii'))<16*1024
    if scenario=='busy':assert rows[0]['status']=='busy' and rows[0]['row'] is None
    if scenario=='normal':
        assert rows[0]['execution']=='22222222-2222-4222-8222-222222222222'
        assert rows[0]['app_instance']=='33333333-3333-4333-8333-333333333333'
    if scenario=='extra-clock-fail':assert meta['extra_clock_errors']==1 and meta['first_extra_clock_rc']==-5
    if scenario=='encode-fail':assert meta['encode_failed']==1 and meta['publish_failed']==0
    if scenario=='wire-fail':assert meta['encode_failed']==0 and meta['publish_failed']==1
    if scenario in ('unmatched','invalid-identity'):
        assert 'UNTRUSTED-INCOMING-TEXT' not in '\n'.join(lines)
        assert all(json.loads(line.split(' ',1)[1])['width'] is None for line in lines if line.startswith('XD_CLOCK_TRANSPORT_TIME '))


@pytest.mark.parametrize('scenario',['normal','encode-fail','wire-fail'])
def test_flag_off_original_calls_results_and_no_new_clock_reads(native,scenario):
    run,*_=native;result,lines=run(scenario,False)
    assert result['reads']==0 and result['reason']==0
    assert result['encode_calls']==1 and result['publish_calls']==(0 if scenario=='encode-fail' else 1)
    assert not any('XD_CLOCK_TRANSPORT' in line for line in lines)


def test_flag_off_optimized_text_matches_original_input_source(native):
    _,directory,common,_=native
    original_path=os.environ.get('XENOVA_CLOCK_DIAG_ORIGINAL_SOURCE')
    if original_path is None:pytest.skip('before-candidate source is supplied only by the isolated closure proof')
    original=Path(original_path)
    assert original.is_file()
    # Remove only debug/UBSan metadata for this exact machine-code comparison;
    # the behavioral scenarios above retain UBSan and the original bounds.
    args=[item for item in common if item not in ('-g','-fsanitize=undefined')]
    sections=[]
    for label,source in (('before',original),('after',HOME/'zephyr/src/lattice_input.c')):
        binary=directory/('off-'+label)
        command=[*args,'-DINPUT_SOURCE="'+str(source)+'"','-o',str(binary)]
        build=subprocess.run(command,capture_output=True,text=True,timeout=120)
        assert build.returncode==0,build.stderr
        text=directory/('off-'+label+'.text')
        extract=subprocess.run(['objcopy','--dump-section','.text='+str(text),str(binary)],capture_output=True,text=True,timeout=10)
        assert extract.returncode==0,extract.stderr
        sections.append(text.read_bytes())
    assert sections[0]==sections[1]
