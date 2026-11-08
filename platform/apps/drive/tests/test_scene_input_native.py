"""기존 state socket callback에 원 SceneGrant를 넣는 native software 회귀."""
from pathlib import Path
import subprocess

import pytest
from test_input_clock import HOME, RUNTIME, HARNESS, reply, schema, encode


SCENE_MAIN=r"""
static uint8_t reply_input[DW_WIRE_MAX],scene_input[DW_WIRE_MAX];
static size_t file_bytes(const char *name,uint8_t *buffer){
 FILE *f=fopen(name,"rb");assert(f);size_t n=fread(buffer,1,DW_WIRE_MAX,f);
 assert(!ferror(f)&&fgetc(f)==EOF);assert(!fclose(f));return n;
}
int main(int argc,char **argv){
 assert(argc==4);
 size_t rn=file_bytes(argv[1],reply_input),sn=file_bytes(argv[2],scene_input);
 const char *test=argv[3];engine=&fixture_engine;dw_pool_init(&engine->maps);
 context_available=true;strcpy(gateway,gateway_id);probe_sent=1000000;auto_probe_sequence=1;
 assert(link_received(NULL,reply_input,rn,INT64_MAX)==0);
 int rc=link_received(NULL,scene_input,sn,INT64_MAX);
 if(!strcmp(test,"foreign-host")||!strcmp(test,"foreign-gateway")||!strcmp(test,"foreign-context")){
  assert(rc!=0&&!engine->maps.scene_seen);puts("wrong current Scene context rejected");return 0;
 }
 assert(rc==0&&engine->maps.scene_seen&&engine->maps.scene_generation==1);
 uint64_t generation=engine->maps.scene_generation,end=engine->maps.scene.valid_until_pi_us;
 assert(dw_scene_generation(&engine->maps,generation,6100000,NULL)==DW_OK);
 if(!strcmp(test,"duplicate")){
  assert(link_received(NULL,scene_input,sn,INT64_MAX)==0);
  assert(engine->maps.scene_generation==generation&&engine->maps.scene.valid_until_pi_us==end);
 }else if(!strcmp(test,"malformed")){
  const uint8_t bad[]={8,2,82,1,0};
  assert(link_received(NULL,bad,sizeof(bad),INT64_MAX)==-EPROTO);
  assert(engine->maps.scene_generation==generation&&dw_scene_generation(&engine->maps,generation,6100000,NULL)==DW_OK);
 }else if(!strcmp(test,"context-loss")){
  struct xd_inputs observed={0};dl_context(&observed);
  assert(dw_scene_generation(&engine->maps,generation,6100000,NULL)==DW_SOURCE);
 }else{
  bool changed=!strcmp(test,"source-change")||!strcmp(test,"source-change-busy");
  bool busy=!strcmp(test,"renew-busy")||!strcmp(test,"source-change-busy");
  bool invalid=!strcmp(test,"invalid-clock");
  assert(changed||busy||invalid||!strcmp(test,"renew"));
  uint64_t original_clock_end=engine->maps.trust.clock.expires_pi_us;
  assert(dw_link_decode(reply_input,rn,&incoming)==DW_OK);
  xenova_drive_DriveLink fresh=incoming;
  if(changed)strcpy(fresh.payload.clock.trust.mapper_instance,"99999999-9999-4999-8999-999999999999");
  if(invalid)fresh.payload.clock.trust.lower_offset_us=fresh.payload.clock.trust.upper_offset_us+1;
  pb_ostream_t stream=pb_ostream_from_buffer(reply_input,sizeof(reply_input));
  assert(pb_encode(&stream,xenova_drive_DriveLink_fields,&fresh));rn=stream.bytes_written;
  fake_now=1010000;probe_sent=1000000;auto_probe_sequence=1;
  if(busy)atomic_flag_test_and_set(&engine->maps.lock);
  assert(link_received(NULL,reply_input,rn,INT64_MAX)==0);
  if(busy)atomic_flag_clear(&engine->maps.lock);
  if(busy||invalid)assert(engine->maps.trust.clock.expires_pi_us==original_clock_end);
  if(changed||invalid){
   if(busy)assert(strcmp(engine->maps.trust.mapper_instance,current_clock.trust.mapper_instance));
   assert(dw_scene_generation(&engine->maps,generation,6100000,NULL)==DW_SOURCE);
   assert(link_received(NULL,scene_input,sn,INT64_MAX)!=0);
  }else assert(engine->maps.scene_generation==generation&&dw_scene_generation(&engine->maps,generation,6100000,NULL)==DW_OK);
 }
 puts("state callback original Scene authority OK");return 0;
}
"""

@pytest.fixture(scope='module')
def scene_input_probe(tmp_path_factory):
    directory=tmp_path_factory.mktemp('scene-state-input')
    include=directory/'include'
    for name in ('xenova','zephyr/sys','zephyr/logging'):
        (include/name).mkdir(parents=True,exist_ok=True)
    (include/'xenova/drive_runtime.h').write_text(RUNTIME)
    for name in ('xenova/drive_context.h','zephyr/sys/atomic.h'):
        (include/name).write_text('#include <xenova/drive_runtime.h>\n')
    (include/'zephyr/logging/log.h').write_text('#define LOG_LEVEL_INF 3\n#define LOG_MODULE_REGISTER(...)\n#define LOG_INF(...) test_log(__VA_ARGS__)\n')
    source=directory/'scene-input.c'
    # 이 회귀는 진단 filter를 켜지 않으므로 원 harness의 미사용 상태만 제외한다.
    prefix=HARNESS.split('int main(',1)[0]
    assert prefix.count('static int test_log_filtered;')==1
    source.write_text(prefix.replace('static int test_log_filtered;','',1)+SCENE_MAIN)
    world=HOME/'world';mqtt=HOME.parent/'common/xenova-mqtt-c'
    binary=directory/'scene-input'
    sources=[source,world/'world.c',world/'generated/world.pb.c',
        *[world/'nanopb'/name for name in ('pb_common.c','pb_decode.c','pb_encode.c')],
        *sorted((mqtt/'src').glob('*.c'))]
    command=['cc','-std=c11','-O1','-g','-Wall','-Wextra','-Werror',
        '-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-Wl,--wrap=dw_link_decode',
        '-fsanitize=undefined',
        *['-I'+str(p) for p in (include,HOME/'zephyr/src',HOME/'planner',world,world/'generated',
                                world/'nanopb',mqtt/'include',mqtt/'third_party/jsmn',HOME.parent/'common/xenova-drive-runtime/include')],
        *map(str,sources),'-lm','-o',str(binary)]
    built=subprocess.run(command,capture_output=True,text=True,timeout=120)
    assert built.returncode==0,built.stderr
    return binary

@pytest.mark.parametrize('case',['duplicate','malformed','context-loss','source-change','renew',
                                 'renew-busy','source-change-busy','invalid-clock','foreign-host','foreign-gateway','foreign-context'])
def test_original_scene_state_callback(scene_input_probe,tmp_path,case):
    clock=reply(map_trust=True);t=clock.clock.trust
    g=schema.SceneGrant(major=1,vehicle=t.vehicle,pi_host_boot=clock.clock.host_boot,
        gateway_instance=clock.clock.gateway_instance,source_admission_revision=4,mapper_device='oak-fixture',
        map_epoch=t.map_epoch,frame_epoch=t.frame_epoch,odom_epoch=t.odom_epoch,
        configuration=t.configuration,calibration=t.calibration,primitive=t.primitive,
        issued_pi_us=6000000,valid_until_pi_us=6500000,lifecycle_state=2,
        request_nonce=bytes([7])*16,survey_deadline_pi_us=6400000,max_observation_stations=24,
        purpose=0,frozen_evidence_sha256=bytes([8])*32,frozen_survey_revision=1,
        frozen_hazard_revision=1,lifecycle_revision=3)
    g.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=bytes([4])*16,admission_revision=1,
        survey_id=bytes([5])*16,survey_revision=1,policy=1,coverage_profile_sha256=bytes([9])*32,
        allow_restore_after_mapper_restart=False))
    if case=='foreign-host':g.pi_host_boot='88888888-8888-4888-8888-888888888888'
    elif case=='foreign-gateway':g.gateway_instance='88888888-8888-4888-8888-888888888888'
    elif case=='foreign-context':g.map_epoch='88888888-8888-4888-8888-888888888888'
    link=schema.DriveLink(major=2);link.scene_grant.CopyFrom(g)
    a=tmp_path/'clock.pb';a.write_bytes(encode(clock))
    b=tmp_path/'scene.pb';b.write_bytes(encode(link))
    result=subprocess.run([str(scene_input_probe),str(a),str(b),case],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert not result.stderr
