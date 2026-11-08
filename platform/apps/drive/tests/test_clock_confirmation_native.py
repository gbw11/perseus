"""실제 C callback의 raw receipt/원 numeric lease. RTOS·송신 경계만 명시 fixture다."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest
from test_input_clock import HOME, RUNTIME, reply
from xenova_drive_world import encode


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
static uint64_t now_us;
static struct drive_engine fixture_engine;
static const char vm[]="11111111-1111-4111-8111-111111111111";
static const char execution[]="22222222-2222-4222-8222-222222222222";
static const char automatic[]="33333333-3333-4333-8333-333333333333";
static const char manual[]="99999999-9999-4999-8999-999999999999";
static const char host[]="44444444-4444-4444-8444-444444444444";
static const char gateway_id[]="55555555-5555-4555-8555-555555555555";
const struct lp_config lp_selected_config={.id="lattice-lab-1",.vehicle="rc-test-1",.calibration="encoder-lab-1",
 .live_age_us=300000,.pose_age_us=200000,.clock_error_us=20000,.clock_lease_us=1000000,.guest_timestamp_error_us=1000,.guest_drift_ppm=100};
const struct lp_assets lp_selected_assets={.sha256="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"};
int xdiag_clock_us(uint64_t *out){*out=now_us;return 0;}
int xd_context_get(struct xt_bootstrap *out){
 memset(out,0,sizeof(*out));strcpy(out->identity.vm_id,vm);strcpy(out->identity.execution_id,execution);
 strcpy(out->identity.host_boot_id,host);strcpy(out->auto_instance_id,automatic);strcpy(out->manual_instance_id,manual);return 0;
}
int k_mutex_lock(struct k_mutex *m,int wait){(void)m;(void)wait;return 0;}
void k_mutex_unlock(struct k_mutex *m){(void)m;}
/* 실제 송신 원 Probe의 immutable boundary다. numeric/ACK 결과를 주입하지 않는다. */
static void offered(unsigned role,uint64_t sequence,uint64_t sent){
 struct guest_clock_slot *s=&guest_clocks[role];s->sequence=sequence;s->sent=sent;
 s->sent_has_receipt=s->pending_valid&&sent<s->pending.expires_guest;
 if(s->sent_has_receipt)s->sent_receipt=s->receipt;
}
static int receive_file(const char *path,uint64_t received){
 uint8_t bytes[DW_WIRE_MAX];FILE *f=fopen(path,"rb");assert(f);size_t n=fread(bytes,1,sizeof(bytes),f);assert(!ferror(f));fclose(f);
 now_us=received;return link_received(NULL,bytes,n,0);
}
int main(int argc,char **argv){
 assert(argc==6);engine=&fixture_engine;dw_pool_init(&engine->maps);dw_odom_init(&engine->odometry);
 strcpy(gateway,gateway_id);context_available=true;control_generation=1;
 bool late=!strcmp(argv[1],"expired"),lowres=!strcmp(argv[1],"lowres");unsigned role=!strcmp(argv[1],"manual")?1:0;struct dl_clock clock={0};uint64_t at=0;
 offered(role,1,1000000);int first_rc=receive_file(argv[2],!strcmp(argv[1],"reject-width")?1100000:lowres?1000000:1010000);
 if(!strncmp(argv[1],"reject",6)){
  assert(first_rc&&clock_get_role(role,&clock,&at)==-EAGAIN&&!guest_clocks[role].pending_valid);
  FILE *out=fopen(argv[5],"w");assert(out);fprintf(out,"{\"first_rc\":%d,\"no_numeric\":true}\n",first_rc);assert(!fclose(out));return 0;
 }
 assert(!first_rc);
 assert(clock_get_role(role,&clock,&at)==-EAGAIN); /* pending alone is not a numeric permission */
 offered(role,2,late?1500000:1050000);int second=receive_file(argv[3],late?1510000:1060000);
 int confirmed=clock_get_role(role,&clock,&at);uint64_t original=confirmed?0:clock.expires_guest;
 uint64_t source=confirmed?0:clock.probe_sequence;bool acknowledged=!confirmed&&clock.acknowledged;
 offered(role,3,late?1550000:1100000);int third=receive_file(argv[4],late?1560000:1110000);
 int recovered=clock_get_role(role,&clock,&at);uint64_t final_seq=recovered?0:clock.probe_sequence;
 now_us=recovered?1900000:clock.expires_guest;int expired=clock_get_role(role,&clock,&at);
 FILE *f=fopen(argv[5],"w");assert(f);
 fprintf(f,"{\"second_rc\":%d,\"confirmed_rc\":%d,\"acknowledged\":%s,\"original_expiry_guest\":%llu,\"original_sequence\":%llu,\"third_rc\":%d,\"recovered_rc\":%d,\"final_sequence\":%llu,\"expired_rc\":%d}\n",
  second,confirmed,acknowledged?"true":"false",(unsigned long long)original,(unsigned long long)source,third,recovered,(unsigned long long)final_seq,expired);
 assert(!fclose(f));return 0;
}
'''


@pytest.fixture(scope='module')
def clock_probe(tmp_path_factory):
    build=tmp_path_factory.mktemp('clock-confirmed');include=build/'include'
    for part in ('xenova','zephyr/sys'):(include/part).mkdir(parents=True,exist_ok=True)
    (include/'xenova/drive_runtime.h').write_text(RUNTIME)
    (include/'xenova/drive_context.h').write_text('#include <xenova/drive_runtime.h>\n')
    (include/'zephyr/sys/atomic.h').write_text('#include <xenova/drive_runtime.h>\n')
    source=build/'clock-confirmed.c';source.write_text(HARNESS)
    mqtt=HOME.parent/'common/xenova-mqtt-c';world=HOME/'world';binary=build/'clock-confirmed'
    includes=(include,HOME/'zephyr/src',HOME/'planner',world,world/'generated',world/'nanopb',mqtt/'include',mqtt/'third_party/jsmn',HOME.parent/'common/xenova-drive-runtime/include')
    sources=[source,world/'world.c',world/'generated/world.pb.c',*sorted((mqtt/'src').glob('*.c')),
             *(world/'nanopb'/name for name in ('pb_common.c','pb_decode.c','pb_encode.c'))]
    command=['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-ffunction-sections','-fdata-sections',
             '-Wl,--gc-sections','-fsanitize=address,undefined',*['-I'+str(p) for p in includes],*map(str,sources),'-lm','-o',str(binary)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stderr
    return binary,build


def exchange(sequence,sent,received,*,role='auto',receipt=None,ack=True,expiry=6_500_000):
    value=reply();r=value.clock;r.probe.sequence=sequence;r.probe.guest_send_us=sent
    if role=='manual':r.probe.app_instance='99999999-9999-4999-8999-999999999999'
    r.pi_receive_us=sent+5_002_000;r.pi_send_us=sent+5_003_000
    r.vehicle.sequence=r.vehicle.source_sample=sequence;r.vehicle.pi_sample_us=sent+5_000_000
    r.vehicle.source_us=sent;r.guest_clock_valid_until_pi_us=expiry
    if receipt:
        seq,p4,raw=receipt;r.probe.receipt.probe_sequence=seq;r.probe.receipt.guest_receive_us=p4
        r.probe.receipt.reply_sha256=hashlib.sha256(raw).digest()
        if ack:r.accepted_receipt.CopyFrom(r.probe.receipt)
    return encode(value)


@pytest.mark.parametrize('role',['auto','manual'])
@pytest.mark.parametrize('second_ack,exact_raw',[(True,True),(False,True),(True,False)],ids=['explicit-ack','echo-is-not-ack','reserialized-digest-is-not-raw'])
def test_actual_raw_receipt_confirms_previous_numeric_without_renewing_original_expiry(clock_probe,role,second_ack,exact_raw):
    binary,build=clock_probe
    # Unknown field remains in the exact transport bytes; SHA is not a protobuf re-encoding.
    first=exchange(1,1_000_000,1_010_000,role=role)+b'\xf8\x07\x01'
    second=exchange(2,1_050_000,1_060_000,role=role,receipt=(1,1_010_000,first if exact_raw else first[:-3]),ack=second_ack,expiry=6_600_000)
    third=exchange(3,1_100_000,1_110_000,role=role,receipt=(2,1_060_000,second),expiry=6_700_000)
    paths=[]
    for name,raw in (('first',first),('second',second),('third',third)):
        path=build/(role+'-'+str(second_ack)+'-'+str(exact_raw)+'-'+name+'.pb');path.write_bytes(raw);paths.append(path)
    output=build/(role+'-'+str(second_ack)+'-'+str(exact_raw)+'.json')
    result=subprocess.run([str(binary),role,*map(str,paths),str(output)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert not result.stderr
    actual=json.loads(output.read_text())
    assert actual['second_rc']==actual['third_rc']==0
    if second_ack and exact_raw:
        assert actual['confirmed_rc']==0 and actual['acknowledged']
        assert actual['original_sequence']==1 and actual['original_expiry_guest']==1_495_899
    else:
        assert actual['confirmed_rc']!=0 and not actual['acknowledged']
    assert actual['recovered_rc']==0 and actual['final_sequence']==2
    assert actual['expired_rc']!=0 # half-open original numeric2 E, no arrival-time renewal



def test_expired_pending_is_dropped_and_next_real_exchange_recovers(clock_probe):
    binary,build=clock_probe
    first=exchange(1,1_000_000,1_010_000)
    # 원 numeric1은 guest1,495,899에서 끝났다. sender는 만료 receipt를 재제공하지 않는다.
    second=exchange(2,1_500_000,1_510_000,expiry=7_500_000)
    third=exchange(3,1_550_000,1_560_000,receipt=(2,1_510_000,second),expiry=7_550_000)
    paths=[]
    for name,raw in (("first",first),("second",second),("third",third)):
        path=build/("expired-"+name+".pb");path.write_bytes(raw);paths.append(path)
    output=build/"expired.json"
    result=subprocess.run([str(binary),"expired",*map(str,paths),str(output)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert not result.stderr
    actual=json.loads(output.read_text())
    assert actual["confirmed_rc"]!=0 and actual["original_sequence"]==0
    assert actual["third_rc"]==actual["recovered_rc"]==0 and actual["final_sequence"]==2
    assert actual["expired_rc"]!=0


def test_low_resolution_raw_interval_is_expanded_before_width_admission(clock_probe):
    binary,build=clock_probe
    value=reply();r=value.clock;r.pi_receive_us=6_000_050;r.pi_send_us=6_000_250
    r.guest_clock_valid_until_pi_us=7_000_050
    first=encode(value)
    # 원Pi service200us가 observed guestRTT0us보다 길지만 명시 error1000us가 이를 감싼다.
    # span1s/100ppm은 보수 분모(1e6-ppm)의 ceil이201us인 정확 경계다.
    second=exchange(2,1_050_000,1_060_000,receipt=(1,1_000_000,first),expiry=7_050_000)
    third=exchange(3,1_100_000,1_110_000,receipt=(2,1_060_000,second),expiry=7_100_000)
    paths=[]
    for name,raw in (("first",first),("second",second),("third",third)):
        path=build/("lowres-"+name+".pb");path.write_bytes(raw);paths.append(path)
    output=build/"lowres.json"
    result=subprocess.run([str(binary),"lowres",*map(str,paths),str(output)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert not result.stderr
    actual=json.loads(output.read_text())
    assert actual["confirmed_rc"]==0 and actual["original_sequence"]==1
    assert actual["original_expiry_guest"]==1_997_799
    assert actual["recovered_rc"]==0 and actual["final_sequence"]==2


@pytest.mark.parametrize("kind",["width","span","service"])
def test_numeric_rejects_outside_error_budget_or_original_support_span(clock_probe,kind):
    binary,build=clock_probe
    value=reply();value.clock.guest_clock_valid_until_pi_us=6_500_000
    if kind=="span":value.clock.guest_clock_valid_until_pi_us=7_002_001
    if kind=="service":value.clock.pi_send_us=6_100_000
    path=build/("rejected-"+kind+".pb");path.write_bytes(encode(value));output=build/("rejected-"+kind+".json")
    result=subprocess.run([str(binary),"reject-"+kind,str(path),str(path),str(path),str(output)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert not result.stderr
    actual=json.loads(output.read_text())
    assert actual["first_rc"]!=0 and actual["no_numeric"]
