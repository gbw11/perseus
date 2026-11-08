"""Scene tag10의 실제 C decoder/pool/planner 회귀. 센서/clock/issuer는 명시 fixture다."""
from pathlib import Path
import struct
import subprocess
from uuid import UUID

import pytest
from test_world import HOME, fixture
from test_engine import lane_grant, modern_world
from xenova_drive_world import schema, encode

def world_for_scene(grant, trust, *, observed_mapper_us=1_000_000):
    """기존 modern geometry와 원 grant/trust를 붙인 명시 retained World fixture."""
    value=modern_world()
    for field in ('vehicle','map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive'):
        setattr(value,field,getattr(grant,field))
    for field in ('mapper_boot','mapper_instance','clock_epoch'):
        setattr(value,field,getattr(trust,field))
    value.observed_us=observed_mapper_us
    value.completed_us=observed_mapper_us+1000
    value.published_us=observed_mapper_us+2000
    value.localization.observed_mapper_us=observed_mapper_us
    value.cell_flags=bytes([11])*(value.width*value.height)
    value.age_le32=struct.pack('<I',3_600_000)*(value.width*value.height)
    value.scene.CopyFrom(grant.scene)
    return value

def native_values(*,live=False):
    w=modern_world()
    t=schema.WorldTrust(vehicle=w.vehicle,mapper_boot=w.mapper_boot,mapper_instance=w.mapper_instance,
        map_epoch=w.map_epoch,frame_epoch=w.frame_epoch,odom_epoch=w.odom_epoch,
        configuration=w.configuration,calibration=w.calibration,primitive=w.primitive,clock_epoch=w.clock_epoch,
        lower_offset_us=100000,upper_offset_us=101000,expires_pi_us=3000000,max_error_us=2000)
    g=schema.SceneGrant(major=1,vehicle=w.vehicle,pi_host_boot=fixture.BOOT,gateway_instance=fixture.INSTANCE,
        source_admission_revision=4,mapper_device='oak-fixture',map_epoch=w.map_epoch,frame_epoch=w.frame_epoch,
        odom_epoch=w.odom_epoch,configuration=w.configuration,calibration=w.calibration,primitive=w.primitive,
        issued_pi_us=1000000,valid_until_pi_us=1280000,lifecycle_state=2,request_nonce=bytes([7])*16,
        survey_deadline_pi_us=1250000,max_observation_stations=24,purpose=0,
        frozen_evidence_sha256=bytes([8])*32,frozen_survey_revision=1,frozen_hazard_revision=1,lifecycle_revision=3)
    g.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=UUID(fixture.FRAME).bytes,admission_revision=1,
        survey_id=UUID(fixture.ODOM).bytes,survey_revision=1,policy=1,
        coverage_profile_sha256=bytes([9])*32,allow_restore_after_mapper_restart=False))
    world=fixture.world() if live else world_for_scene(g,t)
    m=lane_grant()
    m.mission_grant.scene_admission_id=g.scene.admission_id
    m.mission_grant.scene_revision=g.scene.admission_revision
    link=schema.DriveLink(major=2);link.scene_grant.CopyFrom(g)
    return world,link,m

@pytest.fixture(scope='module')
def scene_probe(tmp_path_factory):
    build=tmp_path_factory.mktemp('scene-consumption')
    binary=build/'scene-consumption'
    world=HOME/'world';planner=HOME/'planner'
    sources=[world/'world.c',world/'generated/world.pb.c',
        *[world/'nanopb'/name for name in ('pb_common.c','pb_decode.c','pb_encode.c')],
        planner/'task_reference.c',planner/'lattice.c',planner/'engine.c',planner/'generated/primitives.c',
        HOME/'tests/scene_consumption_probe.c']
    command=['cc','-std=c11','-O2','-g','-Wall','-Wextra','-Werror',
        '-fsanitize=address,undefined',
        *['-I'+str(path) for path in (world,world/'generated',world/'nanopb',planner)],
        *map(str,sources),'-lm','-o',str(binary)]
    built=subprocess.run(command,capture_output=True,text=True,timeout=120)
    assert built.returncode==0,built.stderr
    return binary

def run_external_scene(binary,tmp_path,world_bytes,admitted_link_bytes,revoked_link_bytes,trust_bytes,pi_host,gateway,now_pi_us,*,mission_bytes=None):
    """원 actor/clock bytes를 재발급하지 않고 실제 C gate에 전달한다."""
    paths=[]
    for name,raw in (('world',world_bytes),('admitted',admitted_link_bytes),('revoked',revoked_link_bytes),('trust',trust_bytes)):
        path=tmp_path/(name+'.pb');path.write_bytes(raw);paths.append(path)
    command=[str(binary),'external',*map(str,paths),pi_host,gateway,str(now_pi_us)]
    if mission_bytes is not None:
        path=tmp_path/'mission.pb';path.write_bytes(mission_bytes);command.append(str(path))
    result=subprocess.run(command,capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr
    assert 'external scene decoder/current grant/revoke/expiry OK' in result.stdout
    if mission_bytes is not None:
        assert 'external original actor grant/actual engine held plan fenced OK' in result.stdout
    return result

@pytest.mark.parametrize('case',['gate','duplicate','old','conflict','no-renew','source-hwm',
    'full-ref','policy-ref','source-change','renew','malformed','revoke','restore','new-scene','unadmitted','overflow',
    'engine-no-mission','engine-wrong-mission','engine-mission-null','engine-cancel','engine-expiry',
    'engine-revoke','engine-pending-revoke','engine-apply','engine-apply-expiry','live-isolation','legacy0','legacy1'])
def test_actual_scene_consumption(scene_probe,tmp_path,case):
    world,grant,mission=native_values(live=case=='live-isolation')
    if case.startswith('legacy'):
        world=fixture.world()
        if case=='legacy1':
            from xenova_drive_world import mark_road
            mark_road(world,[schema.Point(x=p.x,y=2.4) for p in world.reference],
                      [schema.Point(x=p.x,y=.8) for p in world.reference])
        world.cell_flags=bytes(flag|2 for flag in world.cell_flags)
        raw=world.SerializeToString(deterministic=True)
        selected='legacy'
    else:
        raw=encode(world);selected=case
    paths=[]
    for name,data in (('world',raw),('grant',encode(grant)),('mission',encode(mission))):
        path=tmp_path/(name+'.pb');path.write_bytes(data);paths.append(path)
    ran=subprocess.run([str(scene_probe),selected,*map(str,paths)],capture_output=True,text=True,timeout=60)
    assert ran.returncode==0,ran.stderr
    assert not ran.stderr
