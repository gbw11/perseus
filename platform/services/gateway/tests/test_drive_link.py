"""Real binary broker/receiver/G2 actor. Paths here are codec fixtures; actual
planner/tracker/state geometry runs in drive/tests/test_engine.py, not mocked there.
"""
import queue
import time
import pytest
from uuid import uuid4

from xenova_drive_world import schema,encode,decode
from xenova_gateway.drive_link import DriveLinkIO,DriveLinkProvider,inbound,outbound
from xenova_mqtt.client import _new_paho
from test_state_transfer import StateWorld
from test_state_transport import broker,until
from test_verified import A,B
from test_odometry import config,observe
from xenova_gateway.odometry import VehicleOdometry


def trust(w):
    return schema.WorldTrust(vehicle='rc-test-1',mapper_boot=str(uuid4()),mapper_instance=str(uuid4()),
        map_epoch=str(uuid4()),frame_epoch=str(uuid4()),odom_epoch=str(uuid4()),configuration='lattice-lab-1',
        calibration='encoder-lab-1',primitive='ab'*32,clock_epoch=str(uuid4()),lower_offset_us=100000,
        upper_offset_us=101000,expires_pi_us=int((w.t+100)*1e6),max_error_us=2000)


def snapshot(w,t):
    link=schema.DriveLink(major=1)
    s=link.snapshot;s.major=1;s.source_vm=A;s.source_execution=w.executions[A]
    s.source_app_instance=w.ids[A]['autonomous'];s.source_generation=w.m.generation;s.state_revision=10
    s.frame_epoch=t.frame_epoch;s.odom_epoch=t.odom_epoch;s.configuration=t.configuration;s.primitive=t.primitive
    s.reference_id='road:lab';s.reference_revision=1;s.path_revision=4;s.maneuver_revision=1
    s.direction=0;s.maneuver_phase=0;s.merge_goal.CopyFrom(schema.Pose(x=1,y=1.6,yaw=0))
    s.vehicle_sequence=2;s.calculation_sequence=3;s.tracker_index=0;s.complete=True
    for x in (.3,.32):s.path.add(pose=schema.Pose(x=x,y=1.6,yaw=0),steering_rad=0,velocity_mps=.2)
    s.message_id=str(uuid4());s.calibration=t.calibration;s.map_epoch=t.map_epoch;s.observation_seq=1
    return link


@pytest.mark.parametrize('direction',[0,1,-1])
def test_actual_binary_socket_state_and_original_g2_acceptance(broker,tmp_path,direction):
    w=StateWorld(tmp_path/'authority');t=trust(w);instance=str(uuid4())
    io=DriveLinkIO('127.0.0.1',broker,None,None,(A,B),clock=lambda:w.t)
    provider=DriveLinkProvider(w.runner,io,t,instance)
    io.start()
    received=queue.Queue();client=_new_paho('binary-app-fixture-'+str(uuid4()))
    client.on_connect=lambda c,u,f,r,*extra:c.subscribe(inbound(B),qos=0)
    client.on_message=lambda c,u,m:received.put(decode(bytes(m.payload),schema.DriveLink))
    client.connect('127.0.0.1',broker,10);client.loop_start()
    try:
        until(lambda:io.thread.is_alive() and client.is_connected())
        body=snapshot(w,t)
        if direction:
            body.snapshot.direction=direction;body.snapshot.maneuver_phase=2
            body.snapshot.maneuver_revision=7 if direction<0 else 1
            body.snapshot.merge_passed_reference_revision=body.snapshot.reference_revision
            body.snapshot.world_features=1
        def submit():
            client.publish(outbound(A),encode(body),qos=0,retain=False)
            end=time.monotonic()+.1
            while time.monotonic()<end:
                item=io.take()
                if item is not None:provider.receive(*item)
                if provider.accepted:return True
                time.sleep(.002)
            return False
        try:until(submit)
        except AssertionError as exc:raise AssertionError((io.first_error,io.received,io.dropped,provider.reasons)) from exc
        until(lambda:not received.empty())
        accepted=received.get(timeout=1)
        assert accepted.WhichOneof('payload')=='accepted' and accepted.accepted.state==body.snapshot
        assert accepted.accepted.state.HasField('merge_passed_reference_revision')==bool(direction)
        assert accepted.accepted.state.world_features==(1 if direction else 0)
        assert body.snapshot.message_id in w.m.states.copies
        applied=schema.DriveLink(major=1);p=applied.applied
        p.vm=B;p.execution=w.executions[B];p.app_instance=w.ids[B]['autonomous'];p.host_boot=w.auth.host;p.gateway_instance=instance
        p.copy_message_id=body.snapshot.message_id;p.state_revision=10
        for name in ('frame_epoch','odom_epoch','configuration','calibration','primitive','reference_id','reference_revision','path_revision'):
            setattr(p,name,getattr(body.snapshot,name))
        p.vehicle_sequence=3;p.calculation_sequence=1;p.rebuilt=False;p.applied=True
        p.maneuver_revision=body.snapshot.maneuver_revision
        p.application_sequence=1
        client.publish(outbound(B),encode(applied),qos=0,retain=False)
        def adopted():
            item=io.take()
            if item is not None:provider.receive(*item)
            return w.m.states.evidence(B,'autonomous') is not None
        until(adopted)
        assert w.m.states.evidence(B,'autonomous')['copy_message_id']==body.snapshot.message_id
        # The same source cannot publish a late update after authority ends.
        w.m.output_permitted=False
        body.snapshot.state_revision+=1;body.snapshot.message_id=str(uuid4())
        assert not provider.receive(A,body,w.t,io.prepare_snapshot(body)) and provider.reasons['STATE_SOURCE_AUTHORITY']==1
        assert io.high_water<=5
    finally:client.disconnect();client.loop_stop();io.close()
    assert not io.thread.is_alive() and io.failure is None


def test_same_copy_semantics_and_actual_context_difference_are_separate(tmp_path):
    w=StateWorld(tmp_path/'authority');t=trust(w)
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B),clock=lambda:w.t)
    provider=DriveLinkProvider(w.runner,io,t,str(uuid4()));body=snapshot(w,t)
    assert provider.receive(A,body,w.t,io.prepare_snapshot(body))
    duplicate=decode(encode(body),schema.DriveLink)
    assert provider.receive(A,duplicate,w.t,io.prepare_snapshot(duplicate))
    assert len(provider.copies)==1
    body.snapshot.path[0].pose.x+=.1
    assert not provider.receive(A,body,w.t,io.prepare_snapshot(body)) and provider.reasons['STATE_ID_CONFLICT']==1
    body.snapshot.source_execution=str(uuid4())
    assert not provider.receive(A,body,w.t,io.prepare_snapshot(body)) and provider.reasons['LINK_EXECUTION']==1


def test_pi_vehicle_clock_is_independent_of_absent_or_expired_mapper_clock(tmp_path):
    w=StateWorld(tmp_path/'authority');source=VehicleOdometry(config(),w.auth.host)
    pi=str(uuid4());source.device_session(str(uuid4()));now=int(w.t*1e6)
    observe(source,pi,now-100000,1000000,1,0,0,quality=5,steer=0,error=10)
    sample=observe(source,pi,now-1000,1099000,2,0,0,quality=5,steer=0,error=10)
    w.runner.device_view=lambda:dict(vehicle_sample=sample)
    for source_trust in (None,trust(w)):
        if source_trust is not None:source_trust.expires_pi_us=1
        io=DriveLinkIO('127.0.0.1',1,None,None,(A,B),clock=lambda:w.t)
        provider=DriveLinkProvider(w.runner,io,source_trust,str(uuid4()))
        request=schema.DriveLink(major=1)
        request.probe.CopyFrom(schema.ClockProbe(vm=B,execution=w.executions[B],app_instance=w.ids[B]['autonomous'],sequence=1,guest_send_us=100))
        assert provider.receive(B,request,w.t)
        response=decode(io.pending[inbound(B),'clock'],schema.DriveLink).clock
        assert response.vehicle.pi_sample_us==sample['pi_sample_us']
        assert response.vehicle.estimated_steering_rad==0 and not response.vehicle.HasField('measured_steering_rad')
        assert response.HasField('trust')==(source_trust is not None)
        if source_trust is not None:assert response.trust.expires_pi_us==1


@pytest.mark.parametrize('first_failure',['oversize','eof'])
def test_binary_oversize_header_is_bounded_and_same_owner_reconnects(first_failure):
    """Actual TCP/Paho receive path, no allocation of the advertised body."""
    import socket
    import struct
    import threading
    from xenova_drive_world import MAX_BYTES
    server=socket.socket();server.bind(('127.0.0.1',0));server.listen(2);server.settimeout(3)
    stop=threading.Event();errors=[]
    message=schema.DriveLink(major=1)
    message.probe.CopyFrom(schema.ClockProbe(vm=A,execution=str(uuid4()),app_instance=str(uuid4()),sequence=1,guest_send_us=100))
    def remaining(n):
        result=bytearray()
        while True:
            byte=n%128;n//=128;result.append(byte|(128 if n else 0))
            if not n:return bytes(result)
    def packet(stream):
        assert stream.recv(1)
        length=0;factor=1
        while True:
            b=stream.recv(1);assert b;length+=(b[0]&127)*factor;factor*=128
            if not b[0]&128:break
        while length:
            part=stream.recv(min(length,1024));assert part;length-=len(part)
    def broker():
        try:
            with server.accept()[0] as first:
                first.settimeout(2);packet(first);first.sendall(b'\x20\x02\x00\x00')
                if first_failure=='oversize':
                    first.sendall(b'\x30'+remaining(MAX_BYTES+257))
                    stop.wait(.2)
            with server.accept()[0] as second:
                second.settimeout(2);packet(second);second.sendall(b'\x20\x02\x00\x00')
                topic=outbound(A).encode();body=struct.pack('!H',len(topic))+topic+encode(message)
                second.sendall(b'\x30'+remaining(len(body))+body);stop.wait(3)
        except BaseException as error:errors.append(error)
    worker=threading.Thread(target=broker);worker.start()
    io=DriveLinkIO('127.0.0.1',server.getsockname()[1],None,None,(A,B)).start()
    try:
        until(lambda:io.received==1)
        item=io.take();assert item[0]==A and item[1]==message
        if first_failure=='oversize':assert io.oversize>=1 and io.drop_reasons['OVERSIZE']>=1
        else:assert io.failed>=1 and io.oversize==0
        assert io.incoming.maxsize==4 and io.failure is None
    finally:
        stop.set();io.close();worker.join(4);server.close()
    assert not worker.is_alive() and not errors


def test_decode_diagnostics_preserve_first_and_bound_recent_causes():
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B))
    for n in range(100):io._drop('INVALID_WIRE',ValueError('field '+str(n)))
    assert io.first_decode_error['reason']=='field 0'
    assert len(io.decode_errors)==8 and io.decode_details_lost==92
    assert io.drop_reasons=={'INVALID_WIRE':100}
