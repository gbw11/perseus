"""G3 uses production pyserial/owner/codec and a PTY, not a fake serial method."""
import importlib.util
import os
from pathlib import Path
import time
from uuid import uuid4
import pytest
from xenova_gateway.feedback import *
from xenova_gateway.frame import DriveFrame,StopFrame,encode
from xenova_gateway.output_owner import OutputOwner
from xenova_gateway.sink import SerialSink,SinkError


def wait(predicate):
    end=time.monotonic()+2
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.003)
    assert predicate()


@pytest.fixture
def device():
    path=Path(__file__).resolve().parents[1]/'examples/virtual_esp32.py'
    spec=importlib.util.spec_from_file_location('virtual_esp32',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    master,slave=os.openpty()
    sink=FeedbackSerialSink(os.ttyname(slave),timeout_ms=400)
    virtual=module.VirtualESP32(master,fragment=True).start()
    owner=OutputOwner(sink,command_timeout_s=.1)
    try:
        wait(lambda:owner.device_view()['ready']);yield owner,sink,virtual
    finally:
        owner.close();virtual.close();os.close(master);os.close(slave)
        assert not owner.thread.is_alive() and not virtual.thread.is_alive()


def test_queued_stop_precedes_feedback_after_idle_wakeup(monkeypatch):
    from threading import Event
    from xenova_gateway.sink import MockSink

    first_poll = Event()
    release_poll = Event()
    idle = Event()
    calls = []

    class FeedbackSink(MockSink):
        snapshot = {}

        def poll(self, now):
            calls.append('poll')
            if not first_poll.is_set():
                first_poll.set()
                assert release_poll.wait(1)

        def write(self, frame, now):
            calls.append('write')
            return super().write(frame, now)

    owner = OutputOwner(FeedbackSink(), command_timeout_s=.1)
    try:
        assert first_poll.wait(1)
        wait_on_condition = owner.condition.wait

        def wait_until_notified(delay):
            idle.set()
            return wait_on_condition(1 if not owner.closing else delay)

        monkeypatch.setattr(owner.condition, 'wait', wait_until_notified)
        release_poll.set()
        assert idle.wait(1)
        owner.write(StopFrame(42, 0, 1), time.monotonic())
        wait(lambda: len(calls) >= 3)
        assert calls[:3] == ['poll', 'write', 'poll']
    finally:
        release_poll.set()
        owner.close()


def test_c13_pty_partial_roundtrip_stages_and_missing_sensor_is_unknown(device):
    owner,sink,virtual=device
    sent=owner.write(DriveFrame(42,0,.1,0),time.monotonic())
    wait(lambda:owner.device_view()['applied'] is True)
    view=owner.device_view()
    assert view['accepted'] and view['command_sequence']==sent.metadata['command_sequence']
    assert view['command_counter']==sent.frame.counter and view['command_generation']==42
    assert view['physical_stop'] is None and view['reported_velocity_mps'] is None
    assert sent.metadata['wire_bytes']>int(sent)
    owner.write(StopFrame(42,0,1),time.monotonic())
    wait(lambda:owner.device_view()['command_kind']=='STOP' and owner.device_view()['applied'])
    assert owner.device_view()['physical_stop'] is None
    assert len(owner.feedback_events())>=2


def test_c13_acceptance_is_not_application_and_timeout_does_not_fabricate_stop(device):
    owner,sink,virtual=device;virtual.apply=False
    owner.write(DriveFrame(42,0,.1,0),time.monotonic())
    wait(lambda:owner.device_view()['accepted'] is True)
    assert owner.device_view()['applied'] is False and owner.device_view()['physical_stop'] is None
    wait(lambda:owner.watchdog_stops==1)
    wait(lambda:owner.device_view()['fault']=='DEVICE_APPLY_TIMEOUT')
    assert owner.device_view()['applied'] is None and owner.device_view()['physical_stop'] is None


def test_c13_old_ack_duplicate_reverse_and_device_boot_invalidate(device):
    owner,sink,virtual=device
    owner.write(DriveFrame(42,0,.1,0),time.monotonic());wait(lambda:owner.device_view()['applied'])
    item=virtual.commands[-1];sample=virtual.sample;before=sink.last_sample
    virtual.send(report(*item,2,sample))
    wait(lambda:sink.duplicates>0);assert sink.last_sample==before
    wrong=(str(uuid4()),*item[1:]);virtual.send(report(*wrong,2,sample+100))
    wait(lambda:sink.rejected>0);assert sink.last_sample==before
    oldboot=owner.device_view()['device_session'];virtual.reboot()
    wait(lambda:owner.device_view()['fault']=='DEVICE_RESTARTED')
    assert owner.device_view()['device_session']!=oldboot and owner.device_view()['applied'] is None
    with pytest.raises(SinkError):owner.write(DriveFrame(42,0,.1,0),time.monotonic())
    owner.write(StopFrame(43,0,1),time.monotonic()) # fault permits protection, never drive


def test_c13_bounded_resynchronization_and_fixed_vector():
    pi='11111111-1111-4111-8111-111111111111';dev='22222222-2222-4222-8222-222222222222'
    frame=StopFrame(42,7,1);wire=command(pi,dev,1,frame)
    assert parse_command(wire)==(pi,dev,1,frame)
    parser=Framer();bad=bytearray(hello(pi));bad[-3]^=1
    data=b'x'*300+b'\0'+bytes(bad)+wire+wire
    output=[]
    for offset in range(0,len(data),7):output+=parser.feed(data[offset:offset+7])
    assert len(output)==2 and parser.invalid==1 and parser.oversize==1 and len(parser.buffer)==0
    # Independent fixed prefix/CRC convention; no change to the legacy payload.
    kind,payload=unpack(wire)
    assert kind==0x0110 and payload[40:]==encode(frame) and payload[32:40]==b'\x01'+b'\0'*7
    import json
    fixed=json.loads((Path(__file__).resolve().parents[4]/'docs/contracts/vectors/gateway-feedback-1.json').read_text())
    assert wire.hex()==fixed['command_stop']
    assert hello(pi).hex()==fixed['hello'] and hello_ack(pi,dev,19).hex()==fixed['hello_ack']
    assert report(pi,dev,1,frame,2,1,stationary=True).hex()==fixed['applied_stationary']


def test_c13_write_only_profile_still_emits_only_legacy_bytes():
    import select
    master,slave=os.openpty();sink=SerialSink(os.ttyname(slave))
    try:
        frame=DriveFrame(42,7,.1,0);assert sink.write(frame,time.monotonic())==len(encode(frame))
        assert select.select([master],[],[],1)[0] and os.read(master,256)==encode(frame)
    finally:sink.close();os.close(master);os.close(slave)


def test_c14_missing_handshake_and_unknown_caps_are_not_ready():
    master,slave=os.openpty();sink=FeedbackSerialSink(os.ttyname(slave),timeout_ms=30)
    owner=OutputOwner(sink,command_timeout_s=.1)
    try:
        wait(lambda:owner.device_view()['fault']=='DEVICE_HANDSHAKE_TIMEOUT')
        assert not owner.device_view()['ready'] and owner.device_view()['applied'] is None
        with pytest.raises(SinkError):owner.write(StopFrame(42,0,1),time.monotonic())
    finally:owner.close();os.close(master);os.close(slave)


def test_c14_physical_report_requires_advertised_sensor_and_corresponding_stop(device):
    owner,sink,virtual=device;virtual.measure=True;virtual.handshake()
    wait(lambda:owner.device_view()['capabilities']&CAP_STATIONARY)
    owner.write(StopFrame(42,0,1),time.monotonic())
    wait(lambda:owner.device_view()['physical_stop'] is True)
    assert owner.device_view()['observation_source']=='device_report'
    assert not isinstance(owner.device_view()['physical_stop'],dict) # evidence is a report, not a physical test


def test_c13_counter_wrap_uses_sequence_and_expired_ack_cannot_restore_evidence(device):
    owner,sink,virtual=device;owner.counter=254
    first=owner.write(StopFrame(42,0,1),time.monotonic())
    wait(lambda:owner.device_view()['applied'])
    second=owner.write(StopFrame(42,0,1),time.monotonic())
    wait(lambda:owner.device_view()['command_sequence']==second.metadata['command_sequence'] and owner.device_view()['applied'])
    assert (first.frame.counter,second.frame.counter)==(255,0)
    assert second.metadata['command_sequence']==first.metadata['command_sequence']+1
    item=virtual.commands[-1];time.sleep(.42)
    old=sink.rejected;virtual.reply(item,2)
    wait(lambda:sink.rejected>old)
    assert owner.device_view()['applied'] is None and owner.device_view()['physical_stop'] is None
    # A fresh, correctly corresponding STOP report works after the expired one.
    owner.write(StopFrame(42,0,1),time.monotonic());wait(lambda:owner.device_view()['applied'])


def test_c14_durable_original_evidence_and_restart_view(device,tmp_path):
    from test_state_transfer import StateWorld
    from test_verified import A,B
    from xenova_gateway.provider import GatewayProvider
    owner,sink,virtual=device;world=StateWorld(tmp_path/'authority')
    world.runner._sink=owner;world.runner._clock=time.monotonic
    world.m.clock=time.monotonic
    # A standby request has no device effect and no fabricated original stage.
    world.t=time.monotonic();world.bind()
    provider=GatewayProvider(world.runner);identity=str(uuid4())
    spec=dict(host_boot_id=world.auth.host,target_vm=B,target_execution_id=world.cache.current(B,world.t)['execution_id'],
              intent_revision=1,lifecycle_generation=1,expected_owner=A,expected_generation=world.m.generation,
              deadline_ms=int(world.t*1000)+1500)
    assert provider.submit(identity,spec)['original']['device'] is None
    # Feed an actual command response into an admitted active protection record.
    spec={**spec,'target_vm':A,'target_execution_id':world.cache.current(A,world.t)['execution_id']}
    active=str(uuid4());provider.submit(active,spec)
    generation=world.auth.value['generation']
    owner.write(StopFrame(generation,0,1),time.monotonic());wait(lambda:owner.device_view()['applied'])
    provider.capture(owner.feedback_events())
    assert provider.view(active)['original']['device']['command_generation']==generation
    before=world.auth.path.read_bytes();provider.capture([]);assert world.auth.path.read_bytes()==before
    world.auth.load();cold=GatewayProvider(world.runner)
    assert cold.view(active)['original']['device']['applied'] and not world.m.output_permitted
    bad=__import__('copy').deepcopy(world.auth.value)
    bad['protections'][-1]['result']['device']['command_generation']+=1
    with pytest.raises(ValueError):world.auth.validate(bad)
