"""실제 PTY/serial adapter/시계/정지/HOLD 흐름. 실제 ESP32 수락 시험은 별도다."""
from dataclasses import replace
import importlib.util
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import UUID,uuid4

import pytest
from xenova_gateway.feedback3 import Feedback3SerialSink,Feedback3Profile
from xenova_gateway.frame import StopFrame,DriveFrame
from xenova_gateway.odometry import VehicleOdometry
from xenova_gateway.output_owner import OutputOwner
from xenova_gateway.scene_authority import StationaryPolicy,COMMAND3
from test_odometry import config
from test_feedback import wait
from test_verified import HOST,A

pytestmark=pytest.mark.skipif(not hasattr(os,'openpty'),reason='Nano/POSIX actual PTY')
GATEWAY=str(uuid4());EXEC=str(uuid4());APP=str(uuid4());MISSION=str(uuid4())


def source(kind='STOP',calc=1,next_direction=1):
    return dict(vm_id=A,execution_id=EXEC,agent_instance_id=APP,app_id='autonomous',generation=7,
        gateway_instance=GATEWAY,direction_intent_admitted=True,command=dict(generation=7,producer_role='AUTO',
        command_kind=kind,calculation_sequence=str(calc),plan_revision='1',phase_revision='1',segment_index=0,cusp_index=0,
        mission_id=MISSION,mission_revision='1',grant_revision='1',driver_session=str(UUID(int=0)),
        driver_action_seq='0',driver_state_seq='0',next_direction=next_direction))


@pytest.fixture
def link():
    master,slave=os.openpty()
    odom=VehicleOdometry(replace(config(),metres_per_tick=.000001,heading_error_rad=.000001),HOST)
    policy=StationaryPolicy(30000,100000,200000,.000001,.05,.1,0.,0.)
    profile=Feedback3Profile('a'*64,'b'*64,100,100,100000,1000000,200000,100,100,policy,10000000,.02,20000)
    spec=importlib.util.spec_from_file_location('virtual_feedback3',Path(__file__).parents[1]/'examples/virtual_feedback3.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    peer=module.VirtualFeedback3(master,'a'*64,'b'*64,drop_first_feedback=True).start()
    sink=Feedback3SerialSink(os.ttyname(slave),timeout_ms=1000,odometry=odom,profile=profile)
    owner=OutputOwner(sink,command_timeout_s=.1)
    try:
        wait(lambda:owner.device_view().get('clock_ready') and owner.device_view().get('vehicle_sample'))
        yield owner,sink,peer
    finally:
        owner.close();peer.close();os.close(master);os.close(slave)


def test_actual_clock_stop_hold_ready_and_immutable_phase(link):
    owner,sink,peer=link;machine=SimpleNamespace(generation=7)
    now=time.monotonic();receipt=owner.write(StopFrame(7,0,1,witness=source()),now)
    wait(lambda:sink.matched_stop is not None)
    def gate():return sink.direction_witness(A,'autonomous',machine,int(time.monotonic()*1e6))
    wait(lambda:gate() is not None)
    wait(lambda:gate() is not None and gate().gate.state==2)
    initial=gate();assert initial.gate.state==2 and initial.gate.requested_direction==1
    end=initial.valid_until_pi_us;serial=initial.gate.stop_command_sequence;revision=initial.gate.gate_revision
    again=owner.write(StopFrame(7,1,1,witness=source(calc=2)),time.monotonic())
    assert again.metadata['command_sequence']==receipt.metadata['command_sequence']
    assert gate().valid_until_pi_us==end and gate().gate.gate_revision==revision
    deadline=time.monotonic()+1
    while time.monotonic()<deadline:
        now=time.monotonic();stamp=int(now*1e6)
        owner.write(DriveFrame(7,3,0,0,issued_pi_us=stamp,expires_pi_us=stamp+90000,witness=source('HOLD_STEER',3)),now)
        if gate() is not None and gate().gate.state==4:break
        time.sleep(.02)
    ready=gate();assert ready is not None and ready.gate.state==4
    assert ready.calculation_sequence==1 and ready.gate.stop_command_sequence==serial and ready.valid_until_pi_us==end
    assert peer.dropped and any(c[10]==3 for c in peer.commands)


def test_clockless_stop_is_physical_only_and_old_job_cannot_hold(link):
    owner,sink,peer=link
    raw=source();raw['direction_intent_admitted']=False
    owner.write(StopFrame(7,0,1,witness=raw),time.monotonic())
    wait(lambda:sink.matched_stop is not None)
    assert sink.direction_witness(A,'autonomous',SimpleNamespace(generation=7),int(time.monotonic()*1e6)) is None
    assert COMMAND3.unpack(sink.matched_stop.command_payload)[3:8]==(bytes(16),0,0,0,0)
