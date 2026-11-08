"""C 완료 원문→Gateway 실제 수신/검산→OutputOwner의 소프트웨어 통합 경계.

AUTO 임무는 실제 operator/journal로 발급한다. lifecycle·Vehicle·장치 연결은
명시 typed 시험 leaf이며 실물 READY, 실제 World planner 또는 MANUAL 주행의
증거가 아니다. Clock I/O 전송은 실제 PreparedClock stamp와 원 P4 ACK를 사용한다.
"""
from hashlib import sha256
import json
import os
import queue
import subprocess
import time
from types import SimpleNamespace

import pytest

from xenova_drive_world import decode, encode, schema
from xenova_gateway.drive_link import inbound
from xenova_gateway.frame import DriveFrame, StopFrame
from xenova_gateway.guest_clock import SentClock
from xenova_gateway.output_owner import OutputOwner
from xenova_mqtt import topics, Identity
from xenova_mqtt.client import Message
from xenova_mqtt.envelope import build
from test_command3_completion_native import command_probe
from test_operator_runtime import world
from test_verified import A, B, BOOT

pytestmark=pytest.mark.skipif(not hasattr(os,'O_DIRECTORY'),reason='actual POSIX Authority journal')


class SnapshotDevice:
    """고정된 typed 장치 관측. poll·출력으로 Vehicle 시각/순번을 갱신하지 않는다."""
    def __init__(self, physical):
        self.physical=physical
        self.snapshot=dict(physical.device_view())
        self.events=queue.Queue()
        self.odometry=SimpleNamespace(config=SimpleNamespace(maximum_sample_age_us=200000))

    def poll(self, now):pass

    def write(self, frame, now):return self.physical.write(frame,now)

    def close(self):self.physical.close()


def confirm_original_clock(w, vm):
    """실제 DriveLink probe 처리/원 stamp/SentClock/P4 수신으로 원 lease를 확인한다."""
    provider=w.r.drive_link;now=w.o.now
    peer=(vm,w.w.executions[vm],w.w.ids[vm]['autonomous'])
    first=schema.DriveLink(major=2)
    first.probe.CopyFrom(schema.ClockProbe(vm=peer[0],execution=peer[1],app_instance=peer[2],
        sequence=7,guest_send_us=995000))
    assert provider.receive(vm,decode(encode(first),schema.DriveLink),w.w.t-.004)
    with w.io.lock:prepared=w.io.pending.pop((inbound(vm),'clock'))
    raw=prepared.stamp(now)
    w.io.clock_completions.put_nowait(SentClock(prepared.peer,prepared.generation,raw))
    provider.tick()
    following=schema.DriveLink(major=2)
    following.probe.CopyFrom(schema.ClockProbe(vm=peer[0],execution=peer[1],app_instance=peer[2],
        sequence=8,guest_send_us=1000000,
        receipt=schema.GuestClockReceipt(probe_sequence=7,reply_sha256=sha256(raw).digest(),guest_receive_us=999000)))
    assert provider.receive(vm,decode(encode(following),schema.DriveLink),w.w.t)
    interval=w.o.clocks.interval(peer,7,1000000,now,w.w.m.generation)
    assert interval is not None
    original=decode(raw,schema.DriveLink).clock
    assert interval[2]==original.guest_clock_valid_until_pi_us
    assert interval[2]==original.pi_receive_us+w.r.guest_clock_policy.lease_us
    return peer,interval


def original_c_command(binary, w, sequence, expiry):
    """원 completion leaf를 실제 C serializer로 보낸다. 반환 JSON은 수정하지 않는다."""
    mission=w.o.missions.current()
    fields=(w.w.m.generation,sequence,1000000,1100000,expiry,7,mission['id'],
        mission['revision'],mission['grant_revision'],19,23,w.sink.vehicle.sequence,10)
    result=subprocess.run([str(binary),'gateway-auto',*map(str,fields)],
        capture_output=True,text=True,timeout=30)
    assert result.returncode==0,(result.stdout,result.stderr)
    assert not result.stderr
    return result.stdout,json.loads(result.stdout)


def receive_original(w, vm, body, sequence, *, emit):
    identity=Identity(vm_id=vm,app_id='autonomous',boot_id=BOOT,
        agent_instance_id=w.w.ids[vm]['autonomous'])
    envelope=build(identity,'drive.command',body,sequence)
    message=Message(topics.vm_app_events(vm,'autonomous'),envelope,w.w.t,False)
    w.r._enqueue(message)
    w.r._drain()
    if emit:w.r._emit(w.w.m.step(w.w.t))
    return message


def ingress_diagnostics(w,vm,owner):
    """제품 판정을 바꾸지 않고 fixture와 실제 수신/출력 경계를 구분해 보존한다."""
    machine=w.w.m;track=machine.nodes[vm]['autonomous']
    return dict(generation=machine.generation,mode=machine.mode.value,phase=machine.phase,
        output_permitted=machine.output_permitted,hold=w.o.hold,ready=machine._ready(vm,'autonomous',w.w.t),
        command_hwm=[(list(key),value) for key,value in w.o.command_hwm.items()],
        last_command_t=track.last_command_t,pending=track.pending,
        source=machine.references.get((vm,'autonomous')),device=w.r.device_view(),
        serial_context_rejected=w.r.serial_context_rejected,serial_failed=w.r.serial_failed,
        submissions=list(w.r.submissions)[-3:],owner_last=repr(owner.last),
        owner_failure=repr(owner.first_failure))


@pytest.mark.parametrize('negative',['expiry','fence'])
def test_actual_c_command3_ingress_adoption_output_and_original_expiry_or_fence(
        world, command_probe, tmp_path, negative):
    w=world
    assert w.apply(w.call('auto.resume'))['result']=='APPLIED'
    assert w.w.m.output_permitted and not w.o.hold
    w.apps();w.tick();w.r.drive_link.tick()
    vm=w.w.m.active_vm
    assert vm in (A,B) and w.w.m.active_app=='autonomous'
    peer,interval=confirm_original_clock(w,vm)
    original_expiry=w.o.now+80000
    raw,body=original_c_command(command_probe,w,41,original_expiry)
    (tmp_path/'original-command3.json').write_text(raw)
    assert body['profile']=='drive-command-3' and body['producer_role']=='AUTO'
    assert body['command_kind']=='DRIVE' and body['clock_probe_sequence']=='7'
    assert body['valid_until_pi_us']==str(original_expiry)
    device=SnapshotDevice(w.sink)
    owner=OutputOwner(device,command_timeout_s=.1,clock=lambda:w.w.t)
    previous=w.r._sink;w.r._sink=owner
    try:
        received=receive_original(w,vm,body,1,emit=True)
        diagnostic=ingress_diagnostics(w,vm,owner)
        (tmp_path/'ingress-diagnostics.json').write_text(json.dumps(diagnostic,indent=2,default=str))
        key=(vm,'autonomous',peer[1],peer[2],w.w.m.generation)
        assert w.o.command_hwm.get(key)==41,diagnostic
        drives=[frame for _,frame,_ in w.sink.frames if isinstance(frame,DriveFrame) and frame.witness]
        assert drives,diagnostic
        frame=drives[-1];source=frame.witness
        assert source['message_id']==received.envelope['message_id']
        assert source['command']==json.loads(raw) and source['execution_id']==peer[1]
        assert frame.v_mps==pytest.approx(.2) and frame.steer_rad==pytest.approx(.1)
        assert frame.issued_pi_us==interval[0]
        assert frame.expires_pi_us==min(original_expiry,interval[0]+100000,interval[2],
            w.sink.vehicle.pi_sample_us+200000)
        assert w.o.command_hwm[key]==41
        drive_count=len(drives)
        # 다른 계산 순번도 C에서 받아 HWM 중복 때문에 거부되는 오판을 피한다.
        rejected_raw,rejected=original_c_command(command_probe,w,42,original_expiry)
        (tmp_path/f'{negative}-command3.json').write_text(rejected_raw)
        if negative=='expiry':
            w.w.t=(original_expiry+1)/1000000
            assert w.o.now>=original_expiry
        else:
            assert w.o.now<original_expiry
            assert w.o.clocks.interval(peer,7,1000000,w.o.now,w.w.m.generation) is not None
            w.o._stop('test-command3-original-fence')
            assert w.o.hold and not w.w.m.output_permitted
        receive_original(w,vm,rejected,2,emit=False)
        assert w.o.command_hwm[key]==41
        assert len([f for _,f,_ in w.sink.frames if isinstance(f,DriveFrame) and f.witness])==drive_count
        end=time.monotonic()+1
        while not isinstance(owner.last,StopFrame):
            assert time.monotonic()<end,'original expiry/fence did not reach owned STOP'
            time.sleep(.001)
        if negative=='expiry':assert owner.watchdog_stops==1
        assert owner.first_failure is None
    finally:
        owner.close();w.r._sink=previous
