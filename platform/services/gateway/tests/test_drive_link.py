"""Real binary broker/receiver/G2 actor. Paths here are codec fixtures; actual
planner/tracker/state geometry runs in drive/tests/test_engine.py, not mocked there.
"""
import queue
import time
import pytest
from uuid import UUID,uuid4

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
    link=schema.DriveLink(major=2)
    s=link.snapshot;s.major=2;s.source_vm=A;s.source_execution=w.executions[A]
    s.source_app_instance=w.ids[A]['autonomous'];s.source_generation=w.m.generation;s.state_revision=10
    s.frame_epoch=t.frame_epoch;s.odom_epoch=t.odom_epoch;s.configuration=t.configuration;s.primitive=t.primitive
    s.reference_id='road:lab';s.reference_revision=1;s.path_revision=4;s.maneuver_revision=1
    s.direction=0;s.maneuver_phase=0;s.merge_goal.CopyFrom(schema.Pose(x=1,y=1.6,yaw=0))
    s.vehicle_sequence=2;s.calculation_sequence=3;s.tracker_index=0;s.complete=True
    for x in (.3,.32):s.path.add(pose=schema.Pose(x=x,y=1.6,yaw=0),steering_rad=0,velocity_mps=.2)
    s.message_id=str(uuid4());s.calibration=t.calibration;s.map_epoch=t.map_epoch;s.observation_seq=1
    s.state_features=7
    s.mission.CopyFrom(schema.MissionExecution(mission_id=bytes(16),mission_revision=0,kind=0,phase=0,
        result=0,operator_request_nonce=bytes(16),mission_deadline_pi_us=0,cancel_fence_revision=0,
        active_segment=0,scene_admission_id=bytes(16),scene_admission_revision=0,strategy=0,
        execution_deadline_pi_us=0,correction_attempts_used=0,execution_travel_m=0.,grant_revision=0,
        search_travel_m=0.,search_deadline_pi_us=0))
    s.segments.add(first_index=0,last_index=1,signed_direction=1)
    s.direction_gate.CopyFrom(schema.DirectionGateRef(gate_revision=0,stop_command_sequence=0,state=0,requested_direction=0))
    return link


def lane_mission(w,t,provider):
    """Controlled issuer fixture for transport checks, not a durable reservation."""
    return schema.MissionGrant(major=1,vehicle=t.vehicle,pi_host_boot=UUID(w.auth.host).bytes,
        gateway_instance=UUID(provider.instance).bytes,owner_vm=UUID(A).bytes,
        owner_execution=UUID(w.executions[A]).bytes,app_instance=UUID(w.ids[A]['autonomous']).bytes,
        control_generation=w.m.generation,mission_id=uuid4().bytes,mission_revision=1,grant_revision=1,
        kind=1,strategy=0,operator_request_nonce=uuid4().bytes,issued_pi_us=int(w.t*1e6),
        mission_deadline_pi_us=0,cancel_fence_revision=0,scene_admission_id=bytes(16),scene_revision=0,
        configuration=t.configuration,calibration=t.calibration,primitive=bytes.fromhex(t.primitive),
        execution_deadline_pi_us=0,correction_attempts_used=0,execution_travel_m=0.,
        max_execution_travel_m=0.,admitted=True,search_deadline_pi_us=0,
        search_travel_m=0.,max_search_travel_m=0.)


def budget_request(w,grant):
    link=schema.DriveLink(major=2)
    link.budget_request.CopyFrom(schema.MissionBudgetRequest(major=1,request_nonce=uuid4().bytes,
        mission_id=grant.mission_id,mission_revision=grant.mission_revision,
        control_generation=w.m.generation,expected_grant_revision=grant.grant_revision,action=1,
        slot_id=1,slot_revision=1,goal_revision=1,plan_revision=1,plan_sha256=bytes([1])*32,
        selected_goal_sha256=bytes([2])*32,scene_admission_id=bytes(16),scene_revision=0,
        completed_guest_us=1000,valid_until_pi_us=int((w.t+.2)*1e6)))
    return link


def test_current_owner_grant_is_delivered_to_both_vms_without_retiming(tmp_path):
    w=StateWorld(tmp_path/'authority');t=trust(w)
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B),clock=lambda:w.t)
    provider=DriveLinkProvider(w.runner,io,t,str(uuid4()));grant=lane_mission(w,t,provider)
    original=encode(grant)
    assert provider.publish_mission(grant)
    for vm in (A,B):
        delivered=decode(io.pending[inbound(vm),'grant'],schema.DriveLink)
        assert delivered.major==2 and encode(delivered.mission_grant)==original
        assert encode(provider._current_mission(vm))==original
    conflict=schema.MissionGrant();conflict.CopyFrom(grant);conflict.issued_pi_us+=1
    assert not provider.publish_mission(conflict) and provider.reasons['MISSION_GRANT_CONFLICT']==1
    stale=schema.MissionGrant();stale.CopyFrom(grant);stale.owner_execution=uuid4().bytes
    assert not provider.publish_mission(stale) and provider.reasons['MISSION_CONTEXT']==1
    grant.issued_pi_us+=123
    assert encode(provider._current_mission(B))==original
    vehicle_source=VehicleOdometry(config(),w.auth.host)
    pi=str(uuid4());vehicle_source.device_session(str(uuid4()));now=int(w.t*1e6)
    observe(vehicle_source,pi,now-100000,1000000,1,0,0,quality=5,steer=0,error=10)
    vehicle=observe(vehicle_source,pi,now-1000,1099000,2,0,0,quality=5,steer=0,error=10)
    w.runner.device_view=lambda:dict(vehicle_sample=vehicle)
    probe=schema.DriveLink(major=2)
    probe.probe.CopyFrom(schema.ClockProbe(vm=B,execution=w.executions[B],
        app_instance=w.ids[B]['autonomous'],sequence=1,guest_send_us=100))
    assert provider.receive(B,probe,w.t)
    clock=decode(io.pending[inbound(B),'clock'],schema.DriveLink).clock
    assert encode(clock.current_mission)==original and clock.vehicle.major==1
    with pytest.raises(ValueError):io.offer('xenova/v1/vehicle/state.pb',original,'grant')


def test_ownerless_budget_uses_authenticated_vm_and_rejects_unconfigured_reservations(tmp_path):
    from hashlib import sha256
    w=StateWorld(tmp_path/'authority');t=trust(w)
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B),clock=lambda:w.t)
    provider=DriveLinkProvider(w.runner,io,t,str(uuid4()));grant=lane_mission(w,t,provider)
    request=budget_request(w,grant)
    assert provider.receive(A,request,w.t)
    assert 'LINK_EXECUTION' not in provider.reasons
    reply=decode(io.pending[inbound(A),'budget'],schema.DriveLink).budget_reply
    assert (reply.result,reply.reason)==(5,1) and not reply.HasField('grant')
    assert reply.request_nonce==request.budget_request.request_nonce
    assert reply.request_sha256==sha256(encode(request.budget_request)).digest()
    assert provider.take_budget_request() is None


def test_budget_port_keeps_original_context_and_delivers_only_exact_completion(tmp_path):
    w=StateWorld(tmp_path/'authority');t=trust(w)
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B),clock=lambda:w.t)
    provider=DriveLinkProvider(w.runner,io,t,str(uuid4()));provider.missions.enable_budget_input()
    grant=lane_mission(w,t,provider);request=budget_request(w,grant)
    assert provider.receive(A,request,w.t)
    original=provider.take_budget_request()
    assert original.vm==A and original.execution==w.executions[A]
    assert provider.receive(A,request,w.t) and provider.take_budget_request() is None
    response=schema.MissionBudgetReply(major=1,request_nonce=original.request_nonce,
        request_sha256=original.request_sha256,result=2,reason=0)
    invalid=schema.MissionBudgetReply();invalid.CopyFrom(response);invalid.request_sha256=bytes(32)
    with pytest.raises(ValueError):provider.complete_budget_request(original,invalid)
    assert provider.complete_budget_request(original,response)
    delivered=decode(io.pending[inbound(A),'budget'],schema.DriveLink).budget_reply
    assert delivered==response and not delivered.HasField('grant')


def test_lane_state_keeps_mission_bytes_and_parking_is_not_misrepresented_as_follow(tmp_path):
    w=StateWorld(tmp_path/'authority');t=trust(w)
    io=DriveLinkIO('127.0.0.1',1,None,None,(A,B),clock=lambda:w.t)
    provider=DriveLinkProvider(w.runner,io,t,str(uuid4()));grant=lane_mission(w,t,provider)
    assert provider.publish_mission(grant)
    body=snapshot(w,t);m=body.snapshot.mission
    for name in ('mission_id','mission_revision','kind','operator_request_nonce','mission_deadline_pi_us',
                 'cancel_fence_revision','scene_admission_id','strategy','execution_deadline_pi_us',
                 'correction_attempts_used','execution_travel_m','grant_revision','search_travel_m','search_deadline_pi_us'):
        setattr(m,name,getattr(grant,name))
    m.phase=10;m.scene_admission_revision=grant.scene_revision
    assert provider.receive(A,body,w.t,io.prepare_snapshot(body))
    accepted=decode(io.pending[inbound(B),'state'],schema.DriveLink).accepted.state
    assert accepted.mission==m and accepted.segments==body.snapshot.segments
    parked=schema.DriveLink();parked.CopyFrom(body);parked.snapshot.message_id=str(uuid4())
    parked.snapshot.mission.kind=2;parked.snapshot.mission.phase=1;parked.snapshot.mission.strategy=1
    parked.snapshot.mission.mission_deadline_pi_us=int((w.t+120)*1e6)
    parked.snapshot.mission.search_deadline_pi_us=int((w.t+90)*1e6)
    parked.snapshot.world_features=15
    assert not provider.receive(A,parked,w.t,io.prepare_snapshot(parked))
    assert provider.reasons['STATE_PARKING_ADMISSION_UNAVAILABLE']==1


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
        applied=schema.DriveLink(major=2);p=applied.applied
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
        request=schema.DriveLink(major=2)
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
    message=schema.DriveLink(major=2)
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
