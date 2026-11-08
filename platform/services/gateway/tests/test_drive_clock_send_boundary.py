"""Real provider snapshots and MQTT send boundary; no firmware or vehicle I/O.

The clock interval calculation below is the fixed guest's lattice_input.c
calculation, with explicit local timing samples rather than new policy limits.
"""
import queue
import threading
from uuid import uuid4

import pytest

from xenova_drive_world import PreparedClock,MAX_BYTES,schema,encode,decode
from xenova_drive_world import codec
from xenova_gateway.drive_link import DriveLinkIO,DriveLinkProvider,inbound
from xenova_mqtt.client import _new_paho
from test_drive_link import trust
from test_odometry import config,observe
from test_state_transfer import StateWorld
from test_state_transport import broker,until
from test_verified import A,B
from xenova_gateway.odometry import VehicleOdometry


def reply(*,receive=0,sent=0):
    value=schema.DriveLink(major=2);body=value.clock
    body.probe.CopyFrom(schema.ClockProbe(vm=A,execution='e',app_instance='a',sequence=1,guest_send_us=10))
    body.host_boot='h';body.gateway_instance='g';body.pi_receive_us=receive;body.pi_send_us=sent
    body.vehicle.CopyFrom(schema.VehicleSample(major=1,vehicle='v',host_boot='h',device_session='d',odom_epoch='o',
        sequence=1,source_sample=1,source_us=7,pi_sample_us=8,clock_error_us=2,left_ticks=-1,right_ticks=3,
        odom_pose=schema.Pose(x=1,y=2,yaw=.3),velocity_mps=.2,position_error_m=.01,heading_error_rad=.02,
        quality=3,calibration='c',estimated_steering_rad=.1,steering_error_rad=.01,gateway_instance='g',pi_session='p'))
    return value


def varint(value):
    result=bytearray()
    while value>127:result.append((value&127)|128);value>>=7
    result.append(value);return bytes(result)


def pad_clock(value,length):
    """Optional unknown field exercises protobuf lengths without new schema."""
    base=len(value.clock.SerializeToString(deterministic=True))
    for count in range(max(0,length-base-5),length-base+1):
        extra=b'\xa2\x06'+varint(count)+b'x'*count
        if base+len(extra)==length:
            value.clock.MergeFromString(extra);return value
    raise AssertionError((base,length))


@pytest.mark.parametrize('stamp',[0,1,127,128,2**35,2**63-1])
def test_uint64_send_splice_matches_canonical_codec_and_preserves_all_other_fields(stamp):
    value=reply();prepared=PreparedClock(value)
    expected=schema.DriveLink();expected.CopyFrom(value);expected.clock.pi_send_us=stamp
    assert prepared.stamp(stamp)==encode(expected)
    assert bytes(prepared)==encode(value) # Each stamp leaves its source unchanged.
    assert decode(prepared.stamp(stamp),schema.DriveLink)==expected


@pytest.mark.parametrize('length',[127,16383])
def test_send_growth_rewrites_enclosing_length_and_retains_unknown_bytes(length):
    value=reply(sent=1);value.clock.probe.vm='v'
    value=pad_clock(value,length);prepared=PreparedClock(value)
    assert len(value.clock.SerializeToString(deterministic=True))==length
    expected=schema.DriveLink();expected.CopyFrom(value);expected.clock.pi_send_us=128
    # One scalar byte and one enclosing length byte are added, including 16KiB.
    assert len(prepared.stamp(128))==len(prepared)+2
    assert prepared.stamp(128)==encode(expected)


def test_size_cap_is_checked_before_splice_allocation():
    # major(2), clock tag(1), clock length(3) plus body fill the same wire cap.
    value=pad_clock(reply(sent=1),MAX_BYTES-6);prepared=PreparedClock(value)
    assert len(prepared)==MAX_BYTES and prepared.stamp(1)==prepared
    with pytest.raises(ValueError,match='wire size cap'):prepared.stamp(2**63-1)


@pytest.mark.parametrize('stamp',[2**63,2**64-1])
def test_send_splice_preserves_the_semantic_timestamp_bound(stamp):
    prepared=PreparedClock(reply());original=bytes(prepared)
    with pytest.raises(ValueError,match='semantic uint64 range'):
        prepared.stamp(stamp)
    assert bytes(prepared)==original


@pytest.mark.parametrize('stamp',[None,False,True,-1,2**64,1.,'1',b'1'])
def test_explicit_invalid_stamp_is_not_a_default(stamp):
    with pytest.raises(ValueError,match='clock uint64 required'):PreparedClock(reply()).stamp(stamp)


def test_time_order_type_topic_and_snapshot_ownership_guards():
    value=reply(receive=10,sent=10);prepared=PreparedClock(value)
    with pytest.raises(ValueError,match='send before receive'):prepared.stamp(9)
    with pytest.raises(ValueError,match='Pi clock order'):PreparedClock(reply(receive=10,sent=9))
    for bad in (b'bytes',schema.DriveLink(major=2),schema.DriveLink(major=2,probe=value.clock.probe)):
        with pytest.raises(ValueError,match='clock reply required'):PreparedClock(bad)
    for name in ('vm','_layout'):
        with pytest.raises(AttributeError,match='immutable'):setattr(prepared,name,0)
        with pytest.raises(AttributeError,match='immutable'):delattr(prepared,name)
    original=bytes(prepared);value.clock.probe.sequence=99;value.clock.vehicle.pi_sample_us=999
    assert bytes(prepared)==original and decode(prepared,schema.DriveLink).clock.probe.sequence==1
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B))
    for topic,data in ((inbound(A),bytes(prepared)),(inbound(B),prepared)):
        with pytest.raises(ValueError,match='LINK_CLOCK_OUTPUT'):io.offer(topic,data,'clock')
    io.offer(inbound(A),prepared,'clock')
    assert io.pending[inbound(A),'clock'] is prepared
    assert io.pending[inbound(A),'clock'].stamp(11)==PreparedClock(reply(receive=10,sent=10)).stamp(11)


def test_same_slot_concurrent_replacement_keeps_each_immutable_snapshot():
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B));start=threading.Barrier(3);last={};errors=[]
    def producer(vm):
        try:
            start.wait()
            for sequence in range(1,101):
                value=reply();value.clock.probe.vm=vm;value.clock.probe.sequence=sequence
                prepared=PreparedClock(value);io.offer(inbound(vm),prepared,'clock')
                value.clock.probe.sequence=0 # Caller mutation after offer cannot reach the slot.
            last[vm]=prepared
        except BaseException as error:errors.append(error)
    threads=[threading.Thread(target=producer,args=(vm,)) for vm in (A,B)]
    for thread in threads:thread.start()
    start.wait()
    for thread in threads:thread.join(2)
    assert not errors and not any(thread.is_alive() for thread in threads)
    assert len(io.pending)==io.high_water==2
    for vm in (A,B):
        assert io.pending[inbound(vm),'clock'] is last[vm]
        assert decode(last[vm].stamp(128),schema.DriveLink).clock.probe.sequence==100


def test_known_pi_queue_work_is_excluded_but_real_transport_over_20ms_remains_rejected(monkeypatch):
    guest_send=1000000;offset=5000000;outbound=1000;pi_work=86000;return_trip=1000
    received=guest_send+offset+outbound
    value=reply(receive=received,sent=received);prepared=PreparedClock(value)
    # Stamp must not repeat encode/validation of the already accepted snapshot.
    monkeypatch.setattr(codec,'encode',lambda value:pytest.fail('second full encode'))
    monkeypatch.setattr(codec,'validate',lambda value:pytest.fail('second validation'))
    monkeypatch.setattr(codec,'decode',lambda *args:pytest.fail('full decode at send'))
    wire=prepared.stamp(received+pi_work)
    parsed=schema.DriveLink();parsed.ParseFromString(wire)
    def width(body,guest_receive):
        lower=body.pi_send_us-guest_receive;upper=body.pi_receive_us-guest_send
        return upper-lower
    guest_receive=guest_send+outbound+pi_work+return_trip
    assert width(value.clock,guest_receive)==88000
    assert width(parsed.clock,guest_receive)==2000 and width(parsed.clock,guest_receive)<=20000
    assert width(parsed.clock,guest_receive+30000)==32000>20000
    assert parsed.clock.vehicle==value.clock.vehicle


def provider_fixture(tmp_path,clock):
    w=StateWorld(tmp_path/'authority');source=VehicleOdometry(config(),w.auth.host)
    pi=str(uuid4());source.device_session(str(uuid4()));now=int(w.t*1e6)
    observe(source,pi,now-100000,1000000,1,0,0,quality=5,steer=0,error=10)
    sample=observe(source,pi,now-1000,1099000,2,0,0,quality=5,steer=0,error=10)
    w.runner.device_view=lambda:dict(vehicle_sample=sample)
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B),clock=clock)
    source_trust=trust(w);source_trust.expires_pi_us=1
    provider=DriveLinkProvider(w.runner,io,source_trust,str(uuid4()))
    request=schema.DriveLink(major=2)
    request.probe.CopyFrom(schema.ClockProbe(vm=B,execution=w.executions[B],app_instance=w.ids[B]['autonomous'],sequence=1,guest_send_us=100))
    return w,io,provider,request


def test_actual_binary_mqtt_stamps_at_publish_and_keeps_expired_source_evidence(broker,tmp_path,monkeypatch):
    now=[0.];w,io,provider,request=provider_fixture(tmp_path,lambda:now[0]);io.port=broker
    now[0]=w.t
    encodes=[];original_encode=codec.encode
    def counted(value):encodes.append(value.WhichOneof('payload'));return original_encode(value)
    monkeypatch.setattr(codec,'encode',counted)
    assert provider.receive(B,request,w.t)
    prepared=io.pending[inbound(B),'clock'];original=decode(prepared,schema.DriveLink)
    assert encodes==['clock']
    now[0]+=.086 # Known Pi work/queue delay; not fresh vehicle/trust evidence.
    received=queue.Queue();subscribed=threading.Event();client=_new_paho('send-boundary-'+str(uuid4()))
    client.on_connect=lambda c,u,f,r,*extra:c.subscribe(inbound(B),qos=0)
    client.on_subscribe=lambda *args:subscribed.set()
    client.on_message=lambda c,u,m:received.put(decode(bytes(m.payload),schema.DriveLink))
    client.connect('127.0.0.1',broker,10);client.loop_start()
    try:
        assert subscribed.wait(2)
        io.start();until(lambda:not received.empty())
        response=received.get(timeout=1)
        assert response.clock.pi_send_us==int(now[0]*1e6)>original.clock.pi_send_us
        response.clock.pi_send_us=original.clock.pi_send_us
        assert response==original
        assert response.clock.trust.expires_pi_us==1
        assert encodes==['clock'] and io.sent==1 and io.failure is None
        # A backwards/bad local clock drops only that optional reply. A later
        # valid sample still uses the same owner and unchanged source evidence.
        now[0]=w.t-.001;io.offer(inbound(B),prepared,'clock')
        until(lambda:io.drop_reasons.get('CLOCK_SEND_TIME')==1)
        assert io.failure is None and io.first_decode_error['reason']=='clock send before receive'
        now[0]=w.t+.1;io.offer(inbound(B),prepared,'clock')
        until(lambda:not received.empty())
        recovered=received.get(timeout=1)
        assert recovered.clock.pi_send_us==int(now[0]*1e6) and recovered.clock.vehicle==original.clock.vehicle
        assert io.sent==2 and encodes==['clock']
    finally:client.disconnect();client.loop_stop();io.close()
    assert not io.thread.is_alive()
