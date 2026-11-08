"""정본 ToF 조립/P4·원TTL와 실제 FD→sink→actor→기존J 포트의 소프트웨어 회귀.

MCU·native reducer·실측 extrinsic/FREE·460800 line·물리 STOP 수락은 아니다.
미승인 pyserial5를 대체하는 시험이 아니며 기존 fixed vector41은 별도 그대로 선택한다.
"""
import json
import os
from pathlib import Path
import socket
import struct
from uuid import UUID,uuid4

import pytest

from xenova_drive_world import schema,decode
from xenova_gateway.feedback import packet
from xenova_gateway.feedback3 import COMMAND,REQUIRED_CAPS
from xenova_gateway.frame import StopFrame
from xenova_gateway.mapper_uplink import _Link,unframe
from xenova_gateway.scene_authority import COMMAND3
from xenova_gateway.tof_feedback3 import (TOF_FRAGMENT3,TOF_COMMON3,TOF_FRAGMENT,TofClock,
    TofInbox,TofAssembler)
from tof_feedback_fixture import selection,fragments,WireBench,HOST,GATEWAY,DEVICE


def typed(*,profiles=((0,'c'*64,4),(1,'d'*64,5))):
    pi=uuid4().bytes;device=UUID(DEVICE).bytes;epoch=uuid4().bytes
    clock=TofClock(epoch,1,100000,100100,1100000,2100000,10)
    return TofAssembler(selection(profiles)),TofInbox(),(pi,device,epoch,True),clock


def submit(inbox,parts,clock,at=1100000):
    for payload in parts:assert inbox.offer(payload,at,clock)


def run(owner,inbox,context,at=1100000):
    loss,items=inbox.drain();return owner.process(loss,items,context,at)


def sample(context,**kw):
    stamp=kw.pop('stamp',998000+min(1000,kw.get('sample',1)-1)*1000)
    return fragments(context[0],context[1],context[2],1,stamp=stamp,**kw)


def test_original_fixed_tof_vectors_decode_to_same_crc_zones_and_source_without_rewriting_vector():
    source=Path(__file__).resolve().parents[4]/'docs/contracts/vectors/gateway-feedback-3.json'
    values=json.loads(source.read_text(encoding='utf-8'));frames=[x for x in values['frames'] if x['name'].startswith('design_tof_fragment_')]
    parts=[bytes.fromhex(row['payload_hex']) for row in frames];first=TOF_FRAGMENT3.unpack(parts[0])
    assert TOF_FRAGMENT3.size==209 and TOF_COMMON3.size==137
    owner=TofAssembler(selection(((first[7],first[11].hex(),first[10]),)))
    inbox=TofInbox();received=first[3]+100000
    clock=TofClock(first[5],first[6],99000,100000,received-10000,received+1000000,0)
    submit(inbox,parts,clock,received)
    raw,_=run(owner,inbox,(first[0],first[1],first[5],True),received)
    assert len(raw)==1;value=decode(raw[0],schema.TofObservation)
    assert value.source_crc32==values['tof_design_only']['sample_crc32']
    assert value.source_us==first[3] and value.source_sample==first[2]
    assert value.sensor_instance==first[8] and value.sensor_generation==first[9]
    zones=b''.join(payload[145:] for payload in parts)
    assert value.distance_mm_le==b''.join(zones[i:i+2] for i in range(0,256,4))
    assert value.status==bytes(zones[i+2] for i in range(0,256,4))
    assert value.quality==bytes(zones[i+3] for i in range(0,256,4))


@pytest.mark.parametrize('order',[(0,1,2,3),(3,1,0,2),(0,0,1,2,3)])
def test_order_duplicates_and_first_fragment_deadline_do_not_retime_capture(order):
    owner,inbox,context,clock=typed();parts,body=sample(context)
    for arrival,index in enumerate(order):assert inbox.offer(parts[index],1100000+arrival,clock)
    raw,_=run(owner,inbox,context,1100010);assert len(raw)==1
    value=decode(raw[0],schema.TofObservation)
    assert value.source_us==998000 and value.pi_sample_us<1100000
    assert owner.sensors[0]['latest']['body']==body and owner.payload_bytes()==393
    submit(inbox,parts,clock,1100100);assert run(owner,inbox,context,1100100)[0]==()
    assert owner.payload_bytes()==393 and owner.counts['duplicate']>=4


@pytest.mark.parametrize('fault',['duplicate-conflict','metadata','CRC32','timeout','missing'])
def test_incomplete_conflicting_corrupt_or_expired_sample_cannot_complete_by_retry(fault):
    owner,inbox,context,clock=typed();parts,_=sample(context)
    submit(inbox,parts[:1],clock);assert run(owner,inbox,context)[0]==()
    if fault=='duplicate-conflict':
        bad=bytearray(parts[0]);bad[-1]^=1;submit(inbox,[bytes(bad)],clock)
    elif fault=='metadata':
        bad=bytearray(parts[1]);struct.pack_into('<I',bad,48,1001);submit(inbox,[bytes(bad)],clock)
    elif fault=='CRC32':
        bad=bytearray(parts[3]);bad[-1]^=1;submit(inbox,[parts[1],parts[2],bytes(bad)],clock)
    elif fault=='missing':submit(inbox,[parts[2],parts[3]],clock)
    else:submit(inbox,parts[1:],clock,1200000)
    at=1200000 if fault in ('timeout','missing') else 1100000
    assert run(owner,inbox,context,at)[0]==()
    submit(inbox,parts,clock,at+1);assert run(owner,inbox,context,at+1)[0]==()
    assert owner.sensors[0]['partial'] is None and owner.sensors[0]['latest'] is None


def test_newer_sample_drops_old_partial_and_independent_sensors_keep_their_sources():
    owner,inbox,context,clock=typed();old,_=sample(context);other,_=sample(context,sensor=1)
    submit(inbox,old[:2],clock);submit(inbox,other,clock)
    complete,_=run(owner,inbox,context);assert len(complete)==1 and decode(complete[0],schema.TofObservation).sensor_id==1
    newer,_=sample(context,sample=2);submit(inbox,newer,clock)
    complete,_=run(owner,inbox,context);assert len(complete)==1 and decode(complete[0],schema.TofObservation).source_sample==2
    submit(inbox,old[2:],clock);assert run(owner,inbox,context)[0]==()
    assert owner.sensors[1]['latest'] is not None and owner.payload_bytes()<=2048


@pytest.mark.parametrize('fault',['old-generation','same-generation-instance','same-instance-generation','generation-wrap'])
def test_sensor_reset_lifetime_does_not_allow_replay_or_instance_relabelling(fault):
    owner,inbox,context,clock=typed();parts,_=sample(context,generation=2);submit(inbox,parts,clock);assert run(owner,inbox,context)[0]
    kwargs={'old-generation':dict(generation=1),'same-generation-instance':dict(generation=2,instance=uuid4().bytes),
        'same-instance-generation':dict(generation=3),'generation-wrap':dict(generation=1)}[fault]
    if fault=='generation-wrap':
        parts,_=sample(context,sample=2,generation=(1<<32)-1,instance=uuid4().bytes)
        submit(inbox,parts,clock);assert run(owner,inbox,context)[0]
    parts,_=sample(context,sample=3,**kwargs);submit(inbox,parts,clock)
    assert run(owner,inbox,context)[0]==()


def test_valid_reset_revokes_only_that_sensor_then_publishes_real_new_instance():
    owner,inbox,context,clock=typed()
    for sensor in (0,1):submit(inbox,sample(context,sensor=sensor)[0],clock)
    assert len(run(owner,inbox,context)[0])==2
    new_instance=uuid4().bytes;parts,_=sample(context,sample=1,generation=2,instance=new_instance,stamp=999000)
    submit(inbox,parts[:1],clock);raw,revoked=run(owner,inbox,context)
    assert raw==() and 0 in revoked and owner.sensors[1]['latest'] is not None
    submit(inbox,parts[1:],clock);raw,_=run(owner,inbox,context)
    assert decode(raw[0],schema.TofObservation).sensor_instance==new_instance
    assert owner.sensors[1]['identity'][1]==1


@pytest.mark.parametrize('fault',['epoch','expired-clock','capture-age','calibration','invalid-counter','timing'])
def test_source_clock_capture_ttl_and_exact_calibration_fail_closed_without_fake_observation(fault):
    owner,inbox,context,clock=typed();parts,_=sample(context)
    if fault=='epoch':clock=TofClock(uuid4().bytes,1,100000,100100,1100000,2100000,10)
    elif fault=='expired-clock':clock=TofClock(clock.epoch,1,100000,100100,1100000,1100000,10)
    elif fault=='capture-age':parts,_=fragments(context[0],context[1],context[2],1,stamp=1000)
    elif fault=='calibration':parts,_=sample(context,calibration='e'*64)
    elif fault=='invalid-counter':parts,_=sample(context,sample=1<<63)
    else:parts,_=sample(context,flags=1)
    submit(inbox,parts,clock);assert run(owner,inbox,context)[0]==()
    assert owner.payload_bytes()<=2048


def test_zone_unknown_free_blocked_and_missing_native_quality_remain_original_not_free_claims():
    owner,inbox,context,clock=typed();zones=bytearray(b''.join(struct.pack('<HBB',900,1,3) for _ in range(64)))
    zones[:4]=struct.pack('<HBB',0,0,0);zones[4:8]=struct.pack('<HBB',1200,1,31);zones[8:12]=struct.pack('<HBB',1300,1,1)
    parts,body=sample(context,zone_data=bytes(zones));submit(inbox,parts,clock)
    raw,_=run(owner,inbox,context);value=decode(raw[0],schema.TofObservation)
    assert (value.status[0],value.quality[0])==(0,0)
    assert value.quality[1]&16 and not value.quality[2]&2
    assert owner.sensors[0]['latest']['body']==body  # Native/free decisions are not invented here.


def test_wire_sensor_error_five_is_preserved_and_never_reinterpreted_as_native_valid_five():
    owner,inbox,context,clock=typed()
    zones=b''.join(struct.pack('<HBB',0,5,0) for _ in range(64))
    parts,_=sample(context,zone_data=zones,flags=2);submit(inbox,parts,clock)
    raw,_=run(owner,inbox,context);value=decode(raw[0],schema.TofObservation)
    assert value.status==bytes([5])*64 and value.quality==bytes(64) and not value.frame_status&1


@pytest.mark.parametrize('distance,status,quality',[(0,1,3),(65535,1,3),(900,8,3),(900,1,32)])
def test_crc_valid_but_invalid_zone_shape_drops_whole_sample(distance,status,quality):
    owner,inbox,context,clock=typed()
    zones=struct.pack('<HBB',distance,status,quality)+b''.join(struct.pack('<HBB',900,1,3) for _ in range(63))
    parts,_=sample(context,zone_data=zones);submit(inbox,parts,clock)
    assert run(owner,inbox,context)[0]==() and owner.payload_bytes()==0
    submit(inbox,parts,clock);assert run(owner,inbox,context)[0]==()


def test_current_source_loss_discards_queued_and_latest_without_restarting_same_sample():
    owner,inbox,context,clock=typed();parts,_=sample(context);submit(inbox,parts,clock)
    assert run(owner,inbox,context)[0]
    next_parts,_=sample(context,sample=2);submit(inbox,next_parts,clock)
    raw,revoked=run(owner,inbox,(*context[:3],False))
    assert raw==() and set(revoked)=={0,1} and owner.payload_bytes()==0
    # A dropped batch is retired too; ACK/source recovery cannot backfill it.
    submit(inbox,next_parts,clock);assert run(owner,inbox,context)[0]==()
    submit(inbox,sample(context,sample=3)[0],clock)
    assert decode(run(owner,inbox,context)[0][0],schema.TofObservation).source_sample==3


def test_latest_expires_from_original_capture_lower_without_refresh_on_actor_poll():
    owner,inbox,context,clock=typed();submit(inbox,sample(context)[0],clock)
    raw,_=run(owner,inbox,context);value=decode(raw[0],schema.TofObservation)
    end=value.pi_sample_us+500000-value.clock_error_us-value.sensor_time_error_us
    assert run(owner,inbox,context,end-1)[0]==() and owner.sensors[0]['latest'] is not None
    raw,revoked=run(owner,inbox,context,end)
    assert raw==() and 0 in revoked and owner.sensors[0]['latest'] is None


def test_overflow_and_missing_ack_fence_whole_source_without_resetting_other_sensor():
    owner,inbox,context,clock=typed()
    submit(inbox,sample(context,sensor=1)[0],clock);assert run(owner,inbox,context)[0]
    parts,_=sample(context)
    for _ in range(8):assert inbox.offer(parts[0],1100000,clock)
    assert not inbox.offer(parts[1],1100000,clock)
    assert run(owner,inbox,context)[0]==() and owner.sensors[1]['latest'] is not None
    submit(inbox,parts,clock);assert run(owner,inbox,context)[0]==()
    owner,inbox,context,clock=typed();parts,_=sample(context)
    inbox.reject(parts[0],'no_ack');assert run(owner,inbox,context)[0]==()
    # Replaying the same sample after ACK recovery cannot restart its deadline.
    submit(inbox,parts,clock);assert run(owner,inbox,context)[0]==()


@pytest.mark.skipif(os.name!='posix',reason='actual explicit development FD')
def test_partial_outer_frame_uses_completing_read_and_crc_loss_only_drops_tof():
    bench=WireBench().install_clock()
    try:
        runner,uplink=bench.actor();parts,_=bench.frame();wire=packet(TOF_FRAGMENT,parts[0])
        bench.b.sendall(wire[:20]);bench.now+=.001;bench.sink.poll(bench.now)
        assert bench.sink.tof_inbox.inputs.empty()
        bench.b.sendall(wire[20:]);bench.now+=.001;bench.sink.poll(bench.now)
        runner._mapper_tof_once();partial=runner._tof_assembler.sensors[0]['partial']
        assert partial['first_received']==int(bench.now*1e6)
        before=(bench.sink.odometry.epoch,bench.sink.clock_sequence,bench.sink.snapshot['vehicle_sample'])
        bad=bytearray(packet(TOF_FRAGMENT,parts[1]));bad[-2]^=1
        bench.b.sendall(bytes(bad));bench.now+=.001;bench.sink.poll(bench.now)
        runner._mapper_tof_once();bench.feed_fragments(parts[2:]);runner._mapper_tof_once()
        assert not uplink._tof_inputs and runner._tof_assembler.sensors[0]['partial'] is None
        assert bench.sink.fault is None and bench.sink.snapshot['clock_ready']
        assert (bench.sink.odometry.epoch,bench.sink.clock_sequence,bench.sink.snapshot['vehicle_sample'])==before
    finally:bench.close()


@pytest.mark.skipif(os.name!='posix',reason='actual explicit development FD')
def test_outer_crc_loss_retires_same_read_batch_before_first_actor_assembly():
    bench=WireBench().install_clock()
    try:
        runner,uplink=bench.actor();parts,_=bench.frame()
        bad=bytearray(packet(TOF_FRAGMENT,parts[0]));bad[-2]^=1
        bench.b.sendall(bytes(bad)+b''.join(packet(TOF_FRAGMENT,row) for row in parts))
        bench.now+=.001;bench.sink.poll(bench.now);runner._mapper_tof_once()
        assert not uplink._tof_inputs and bench.sink.tof_inbox.counts['outer_frame_loss']>0
        bench.feed_fragments(parts);runner._mapper_tof_once();assert not uplink._tof_inputs
        bench.feed_fragments(bench.frame(sample=2)[0]);runner._mapper_tof_once()
        assert decode(uplink._tof_inputs[0],schema.TofObservation).source_sample==2
        assert bench.sink.fault is None and bench.sink.snapshot['clock_ready']
    finally:bench.close()


@pytest.mark.skipif(os.name!='posix',reason='actual explicit development FD')
def test_actual_serial_parser_ack_mapping_actor_and_existing_uplink_preserve_one_original_observation():
    bench=WireBench().install_clock();left=right=None
    try:
        runner,uplink=bench.actor();parts,body=bench.frame();before=dict(bench.sink.snapshot);epoch=bench.sink.odometry.epoch
        bench.feed_fragments(parts);runner._mapper_tof_once()
        assert len(uplink._tof_inputs)==1
        raw=uplink._tof_inputs[0];value=decode(raw,schema.TofObservation)
        assert value.pi_session==bench.sink.pi_session and value.clock_epoch==bench.sink.clock_epoch
        assert value.source_us==TOF_FRAGMENT3.unpack(parts[0])[3] and value.pi_sample_us<int(bench.now*1e6)
        assert value.sensor_time_error_us==1000 and bench.sink.odometry.epoch==epoch
        assert set(bench.sink.snapshot)==set(before) and bench.sink.snapshot['clock_ready']
        assert uplink.offer_tof(raw)
        queued=uplink.pending[('tof',0)];expected=value.pi_sample_us+500000-value.clock_error_us-value.sensor_time_error_us
        assert queued[1]==raw and queued[3]==expected
        left,right=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        identities=(HOST,GATEWAY,str(uuid4()),str(uuid4()));link=_Link(left,identities)
        assert link.offer(queued[0],queued[1],queued[2],queued[3],queued[4]);link.flush(int(bench.now*1e6))
        header,preserved=unframe(right.recv(4096));assert header['kind']=='SOURCE_TOF' and preserved==raw
        assert int(header['expires_pi_us'])==expected
        assert not uplink.offer_tof(raw)  # Retry is not a new source/E.
        newparts,_=bench.frame(generation=2,instance=uuid4().bytes)
        bench.feed_fragments(newparts);runner._mapper_tof_once()
        assert ('tof',0) not in uplink.pending
        current=uplink._tof_inputs[0];assert uplink.offer_tof(current)
        with pytest.raises(ValueError,match='J_TOF_SOURCE_LIFETIME'):uplink.offer_tof(raw)
        assert bench.sink.odometry.epoch==epoch
    finally:
        if left is not None:left.close();right.close()
        bench.close()


@pytest.mark.parametrize('available',[False,True])
@pytest.mark.skipif(os.name!='posix',reason='actual explicit development FD')
def test_optional_capability_and_queue_pressure_do_not_block_clockless_stop_or_encoder(available):
    bench=WireBench(capabilities=REQUIRED_CAPS|(0x200 if available else 0))
    try:
        if available:bench.install_clock()
        baseline=(bench.sink.odometry.epoch,bench.sink.clock_sequence,bench.sink.fault)
        for n in range(1,10):bench.feed_fragments(bench.frame(sample=n)[0][:1])
        if available:assert bench.sink.tof_inbox.counts['overflow']>0
        assert (bench.sink.odometry.epoch,bench.sink.clock_sequence,bench.sink.fault)==baseline
        bench.sink.write(StopFrame(7,0,1),bench.now)
        commands=[COMMAND3.unpack(raw) for kind,raw in bench.outgoing() if kind==COMMAND]
        assert commands and commands[-1][10]==2 and commands[-1][3:8]==(bytes(16),0,0,0,0)
        assert bench.sink.fault is None  # Submission is not an APPLIED/physical STOP claim.
    finally:bench.close()


@pytest.mark.skipif(os.name!='posix',reason='actual explicit development FD')
def test_empty_selection_and_no_ack_never_create_tof_or_require_new_control_capability():
    for profiles in ((),((0,'c'*64,4),)):
        bench=WireBench(profiles=profiles,capabilities=REQUIRED_CAPS)
        try:
            before=bench.sink.odometry.epoch;parts,_=bench.frame();bench.feed_fragments(parts)
            loss,items,context=bench.sink.tof_inputs()
            owner=TofAssembler(selection(profiles));assert owner.process(loss,items,context,int(bench.now*1e6))[0]==()
            assert bench.sink.ready and bench.sink.fault is None and bench.sink.odometry.epoch==before
        finally:bench.close()


@pytest.mark.skipif(os.name!='posix',reason='actual explicit development FD')
def test_actual_missing_install_ack_drops_whole_sample_instead_of_backfilling_after_recovery():
    bench=WireBench()
    try:
        old,_=bench.frame();bench.feed_fragments(old)
        assert bench.sink.tof_inbox.inputs.empty() and bench.sink.tof_inbox.counts['invalid']>0
        bench.install_clock();runner,uplink=bench.actor();runner._mapper_tof_once()
        bench.feed_fragments(old);runner._mapper_tof_once();assert not uplink._tof_inputs
        new,_=bench.frame(sample=2);bench.feed_fragments(new);runner._mapper_tof_once()
        assert decode(uplink._tof_inputs[0],schema.TofObservation).source_sample==2
        assert bench.sink.fault is None and bench.sink.snapshot['clock_ready']
    finally:bench.close()
