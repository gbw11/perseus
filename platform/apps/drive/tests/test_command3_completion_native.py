"""실제 lattice app의 불변 완료→command3와 원 cusp STOP pin. RTOS는 명시 fixture다."""
import json
from pathlib import Path
import subprocess

import pytest
from test_input_clock import HOME, RUNTIME


HARNESS=r'''
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lattice_app.c"
int k_mutex_lock(struct k_mutex *m,int wait){(void)m;(void)wait;return 0;}
void k_mutex_unlock(struct k_mutex *m){(void)m;}
static int fixture_clock_error;
static const char fixture_vm[]="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
static const char fixture_sid[]="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
int xdiag_clock_us(uint64_t *now){*now=1000000;return 0;}
int xd_context_get(struct xt_bootstrap *boot){memset(boot,0,sizeof(*boot));strcpy(boot->identity.vm_id,fixture_vm);strcpy(boot->identity.host_boot_id,fixture_vm);return 0;}
int dl_manual_clock_get(struct dl_clock *out,uint64_t *now){
 if(fixture_clock_error)return -EAGAIN;
 *out=(struct dl_clock){.acknowledged=true,.expires_guest=2000000,.expires_pi=2000000,.probe_sequence=7};
 strcpy(out->trust.odom_epoch,fixture_sid);strcpy(out->gateway,fixture_vm);*now=1000000;return 0;
}
int dl_clock_get(struct dl_clock *out,uint64_t *now){return dl_manual_clock_get(out,now);}
static xenova_drive_DirectionGateWitness fixture_gate;static bool fixture_gate_available;
int dl_direction_get(bool role,xenova_drive_DirectionGateWitness *out,uint64_t now){(void)role;(void)now;if(!fixture_gate_available)return -EAGAIN;*out=fixture_gate;return 0;}
static uint64_t context_u64(const char *text){
 char *end;errno=0;unsigned long long value=strtoull(text,&end,10);
 assert(*text&&*text!='-'&&!*end&&!errno&&value<=INT64_MAX);return (uint64_t)value;
}
int main(int argc,char **argv){
 assert(argc==2||argc==3||argc==15);struct drive_completion work={.sequence=41,.end_us=1000000};
 work.sample.modern_command=true;work.sample.v_mps=.2f;work.sample.steer_rad=.1f;
 work.sample.valid_until_guest_us=1100000;work.sample.command.clock_probe_sequence=7;
 struct xm_drive_command2 *body=&work.sample.command.command;
 body->generation=2;strcpy(body->producer_role,"AUTO");strcpy(body->command_kind,"STOP");
 strcpy(body->mission_id,"11111111-1111-4111-8111-111111111111");strcpy(body->driver_session,nil_uuid);
 body->mission_revision=3;body->grant_revision=4;body->vehicle_sequence=9;body->world_sequence=10;
 body->plan_revision=19;body->phase_revision=23;body->valid_until_pi_us=6500000;
 struct xm_drive_command3 command;uint64_t until=0;
 assert(!drive_lattice_command(&work,&command,&until));
 assert(command.clock_probe_sequence==7&&command.command.calculation_sequence==41&&command.command.completed_guest_us==1000000);
 assert(command.command.valid_until_pi_us==6500000&&until==1100000&&!strcmp(command.command.command_kind,"DRIVE"));
 assert(command.next_direction==0&&command.command.v_mps==.2f);
 if(!strcmp(argv[1],"gateway-auto")){
  /* Gateway가 소유한 원 scope/시각을 completion 입력으로 받는다. 출력 JSON을
   * 재명명하거나 실제 planner/물리 입력을 생성했다는 근거로 사용하지 않는다. */
  assert(argc==15);uint64_t generation=context_u64(argv[2]);assert(generation&&generation<=UINT32_MAX);
  body->generation=(uint32_t)generation;work.sequence=context_u64(argv[3]);work.end_us=context_u64(argv[4]);
  work.sample.valid_until_guest_us=context_u64(argv[5]);body->valid_until_pi_us=context_u64(argv[6]);
  work.sample.command.clock_probe_sequence=context_u64(argv[7]);assert(strlen(argv[8])==36);strcpy(body->mission_id,argv[8]);
  body->mission_revision=context_u64(argv[9]);body->grant_revision=context_u64(argv[10]);
  body->plan_revision=context_u64(argv[11]);body->phase_revision=context_u64(argv[12]);
  body->vehicle_sequence=context_u64(argv[13]);body->world_sequence=context_u64(argv[14]);
  assert(work.sequence&&work.end_us&&work.sample.valid_until_guest_us>work.end_us);
  assert(!drive_lattice_command(&work,&command,&until));
 }else if(!strncmp(argv[1],"manual-",7)){
  assert(argc==3);char raw[1025];FILE *f=fopen(argv[2],"rb");assert(f);size_t length=fread(raw,1,1024,f);assert(!ferror(f));assert(!fclose(f));raw[length]=0;
  char control[512];int count=snprintf(control,sizeof(control),"{\"generation\":2,\"mode\":\"MANUAL\",\"active_app\":\"manual\",\"active_vm_id\":\"%s\",\"manual_session\":\"%s\",\"manual_policy\":\"CONTINUE_SESSION\"}",fixture_vm,fixture_sid);assert(count>0);
  struct xm_span selected={control,(uint16_t)count},input={raw,(uint16_t)length};
  dw_odom_init(&engine.odometry);dw_vehicle vehicle={.pi_sample_us=1000000,.clock_error_us=500,.velocity_mps=.05f,.quality=3,
   .has_measured_steering_rad=true,.has_steering_error_rad=true,.steering_error_rad=.001f};
  strcpy(vehicle.host_boot,fixture_vm);strcpy(vehicle.odom_epoch,fixture_sid);vehicle.sequence=9;
  vehicle.has_gateway_instance=vehicle.has_pi_session=true;strcpy(vehicle.gateway_instance,fixture_vm);strcpy(vehicle.pi_session,fixture_vm);
  strcpy(vehicle.vehicle,"rc-test-1");strcpy(vehicle.calibration,"encoder-lab-1");
  assert(!dw_odom_add(&engine.odometry,&vehicle));manual_cfg_default(&manual_config);
  memset(&work.sample,0,sizeof(work.sample));
  if(!strcmp(argv[1],"manual-unavailable")){
   fixture_clock_error=-EAGAIN;assert(!manual(NULL,selected,input,0,1200000,&work.sample));
   assert(manual_gate.continuation_closed&&!manual_gate.seen);fixture_clock_error=0;
  }
  memset(&work.sample,0,sizeof(work.sample));assert(!manual(NULL,selected,input,0,1200000,&work.sample));
  if(!strcmp(argv[1],"manual-neutral")){
   assert(!manual_qualified(NULL,input,0)&&work.sample.v_mps==0&&!work.sample.reason);
   assert(!manual_gate.seen&&!manual_gate.continuation_closed);
  }else if(!strcmp(argv[1],"manual-unavailable"))assert(work.sample.v_mps==0&&work.sample.reason);
  else{assert(!strcmp(argv[1],"manual-continue"));assert(work.sample.v_mps>0&&!work.sample.reason&&!manual_gate.used_stop_sequence);}
  assert(!drive_lattice_command(&work,&command,&until));
 }else if(!strcmp(argv[1],"stop")){
  work.sample.reason="CLOCK_UNAVAILABLE";work.sample.command.clock_probe_sequence=0;work.sample.valid_until_guest_us=0;
  assert(!drive_lattice_command(&work,&command,&until));
  assert(!strcmp(command.command.command_kind,"STOP")&&!command.clock_probe_sequence&&!command.command.valid_until_pi_us);
  assert(command.command.v_mps==0&&command.command.steer_rad==0&&until==0);
 }else if(!strcmp(argv[1],"phase")){
  work.sample.v_mps=work.sample.steer_rad=0;work.sample.gate_required=true;work.sample.requested_direction=-1;
  direction_phase=(struct direction_phase){.active=true,.generation=2,.segment=0,.next_direction=-1,.maneuver_revision=23,.end=9000000};
  assert(!drive_lattice_command(&work,&command,&until));drive_lattice_command_attempt(&work,&command);
  assert(!direction_phase.has_stop&&direction_phase.attempt_count==1&&direction_phase.attempts[0].calculation_sequence==41&&direction_phase.end==9000000);
  work.sequence=42;assert(!drive_lattice_command(&work,&command,&until));drive_lattice_command_attempt(&work,&command);
  assert(direction_phase.attempt_count==2&&direction_phase.attempts[0].calculation_sequence==41&&direction_phase.attempts[1].calculation_sequence==42&&direction_phase.end==9000000);
  /* 명시 원격 STOPPING 진술: 로컬 첫 offer41이 없어도 actual remote42와만 결박한다. */
  fixture_gate=(xenova_drive_DirectionGateWitness){.control_generation=2,.producer_role=1,.calculation_sequence=42,
   .plan_revision=19,.phase_revision=23,.mission_revision=3,.grant_revision=4,.valid_until_pi_us=9000000};
  fixture_gate.gate=(xenova_drive_DirectionGateRef){.gate_revision=7,.stop_command_sequence=9001,.state=1,.requested_direction=-1};
  const uint8_t mission[16]={0x11,0x11,0x11,0x11,0x11,0x11,0x41,0x11,0x81,0x11,0x11,0x11,0x11,0x11,0x11,0x11};
  fixture_gate.mission_id.size=fixture_gate.driver_session.size=16;memcpy(fixture_gate.mission_id.bytes,mission,16);fixture_gate_available=true;
  struct dl_clock original_clock={.acknowledged=true};dw_vehicle unused={0};
  /* 실제 phase와 다른 방향 진술은 원 STOP 후보를 pin하지 않는다. */
  fixture_gate.gate.requested_direction=1;assert(isnan(advance_direction(&original_clock,1000000,&unused)));
  assert(!direction_phase.has_stop&&direction_phase.attempt_count==2&&direction_phase.end==9000000);
  fixture_gate.gate.requested_direction=-1;
  assert(isnan(advance_direction(&original_clock,1000000,&unused)));
  assert(direction_phase.has_stop&&direction_phase.original_stop.calculation_sequence==42&&direction_phase.end==9000000);
  fixture_gate.calculation_sequence=41;assert(isnan(advance_direction(&original_clock,1000000,&unused)));
  assert(direction_phase.original_stop.calculation_sequence==42);
  work.sample.hold_steer=true;work.sample.steer_rad=0;
  assert(!drive_lattice_command(&work,&command,&until));
  assert(!strcmp(command.command.command_kind,"HOLD_STEER")&&command.next_direction==-1);
  drive_lattice_command_attempt(&work,&command);
  assert(direction_phase.attempt_count==2&&direction_phase.original_stop.calculation_sequence==42&&direction_phase.end==9000000);
 }else assert(!strcmp(argv[1],"original"));
 char encoded[XM_COMMAND2_BYTES+1];int n=xm_drive_command3_json(encoded,sizeof(encoded),&command);assert(n>0);
 puts(encoded);return 0;
}
'''


@pytest.fixture(scope='module')
def command_probe(tmp_path_factory):
    build=tmp_path_factory.mktemp('command3-completion');include=build/'include'
    for part in ('xenova','zephyr/sys','zephyr/random'):(include/part).mkdir(parents=True,exist_ok=True)
    # Public app context is a borrowed original snapshot; these fields match the actual header.
    runtime=RUNTIME.replace('struct xd_inputs {bool connected;',
        'struct xd_inputs {char app_instances[2][37],vm_id[37];bool connected;')
    (include/'xenova/drive_runtime.h').write_text(runtime)
    (include/'xenova/drive_context.h').write_text('#include <xenova/drive_runtime.h>\n')
    (include/'zephyr/sys/atomic.h').write_text('#include <xenova/drive_runtime.h>\n')
    (include/'zephyr/random/random.h').write_text('#include <stddef.h>\nvoid sys_rand_get(void *,size_t);\n')
    (include/'shim.h').write_text('#define XENOVA_DRIVE_IO_H\n#include <xenova/drive_runtime.h>\nvoid k_yield(void);\n')
    source=build/'completion.c';source.write_text(HARNESS)
    platform=HOME.parents[1];mqtt=platform/'apps/common/xenova-mqtt-c';world=HOME/'world';binary=build/'completion'
    includes=(include,HOME/'zephyr/src',HOME/'planner',world,world/'generated',world/'nanopb',mqtt/'include',mqtt/'third_party/jsmn',
        platform/'apps/common/xenova-drive-runtime/include',platform/'apps/autonomous/zephyr/src',
        platform/'apps/manual/zephyr/src',platform/'guests/zephyr/diagnostics/include')
    command=['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror','-ffunction-sections','-fdata-sections',
        '-Wl,--gc-sections','-fsanitize=address,undefined','-include',str(include/'shim.h'),
        *['-I'+str(p) for p in includes],str(source),*[str(HOME/'planner'/name) for name in ('task_reference.c','lattice.c','engine.c','generated/primitives.c')],str(platform/'apps/manual/zephyr/src/manual.c'),
        str(world/'world.c'),str(world/'generated/world.pb.c'),*[str(world/'nanopb'/name) for name in ('pb_common.c','pb_decode.c','pb_encode.c')],
        *map(str,sorted((mqtt/'src').glob('*.c'))),'-lm' ,'-o',str(binary)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=120)
    assert result.returncode==0,result.stderr
    return binary


@pytest.mark.parametrize('case',['original','stop','phase'])
def test_actual_completion_does_not_replace_original_clock_job_or_phase_budget(command_probe,case):
    result=subprocess.run([str(command_probe),case],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert not result.stderr
    body=json.loads(result.stdout)
    assert body['profile']=='drive-command-3'
    assert body['calculation_sequence']==('42' if case=='phase' else '41')
    assert body['completed_guest_us']=='1000000'
    assert body['clock_probe_sequence']==('0' if case=='stop' else '7')
    assert body['command_kind']=={'original':'DRIVE','stop':'STOP','phase':'HOLD_STEER'}[case]
    assert body['next_direction']==(-1 if case=='phase' else 0)


@pytest.mark.parametrize("case",["neutral","continue","unavailable"])
def test_actual_manual_caller_zero_preparation_and_moving_first_b_are_separate(command_probe,tmp_path,case):
    # Current numeric clock/Vehicle and Pi CONTINUE authority are explicit external fixtures here.
    # Their raw receipt/physical producers are tested separately; no synthetic STOP number is created.
    value=dict(profile="manual-input-2",session_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        producer_kind="NEUTRAL_ONLY" if case=="neutral" else "PULSE",sample_seq="1",action_seq="0" if case=="neutral" else "1",
        state_seq="1",generation=1,keys=[] if case=="neutral" else ["up"],valid=True,source_pi_us="1000000",
        action_expires_pi_us="0" if case=="neutral" else "1100000",state_reason="NEUTRAL" if case=="neutral" else "USER_NUDGE")
    path=tmp_path/"original-input.json";path.write_text(json.dumps(value,separators=(",",":")))
    result=subprocess.run([str(command_probe),"manual-"+case,str(path)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert not result.stderr
    body=json.loads(result.stdout)
    assert body["producer_role"]=="MANUAL" and body["clock_probe_sequence"]=="7"
    assert body["driver_session"]==value["session_id"] and body["driver_state_seq"]=="1"
    assert body["command_kind"]==("DRIVE" if case=="continue" else "STOP")
    assert body["next_direction"]==0
