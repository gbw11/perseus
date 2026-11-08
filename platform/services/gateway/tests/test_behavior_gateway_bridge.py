"""Actual C app/input/codec ↔ Gateway actor/clock/registry integration on Nano.

RTOS scheduling, authenticated pipe transport, placed World and stationary plant
are explicit leaves. No READY/clock confirmation/registration receipt is injected.
The pre-existing operator-world lifecycle is only the initial control premise;
actual shared C lifecycle output is retained as evidence, not replaced by it.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import select
import subprocess
import time
from uuid import uuid4

import pytest
from xenova_drive_world import schema,encode,decode,episode_request_body,budget_request_body
from xenova_gateway.drive_link import inbound
from xenova_gateway.guest_clock import OwnedPreparedClock,SentClock
from xenova_gateway.frame import DriveFrame
from xenova_gateway.output_owner import OutputOwner
from test_behavior_app_bridge import app_bridge_binary
from test_command3_gateway_flow import SnapshotDevice,receive_original,ingress_diagnostics
from test_engine import modern_world
from test_lattice import obstacle
from test_operator_runtime import world
from b08_fixture import activate,journal


class App:
    def __init__(self,binary,stderr):
        self.process=subprocess.Popen([str(binary)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=stderr,bufsize=0)
        self.buffer=b'';self.transcript=[]

    def ask(self,line):
        self.process.stdin.write(line.encode()+b'\n');self.process.stdin.flush()
        output=[];until=time.monotonic()+15
        while True:
            if b'\n' not in self.buffer:
                remaining=until-time.monotonic();assert remaining>0,'actual C bridge response timeout'
                assert select.select([self.process.stdout],[],[],remaining)[0],'actual C bridge stalled'
                value=os.read(self.process.stdout.fileno(),65536)
                assert value,'actual C bridge exited before END'
                self.buffer+=value;continue
            raw,self.buffer=self.buffer.split(b'\n',1);text=raw.decode();output.append(text)
            if text.startswith('END '):
                _,result,guest=text.split();self.transcript.append((line,output))
                return int(result),int(guest),output[:-1]

    def close(self):
        if self.process.poll() is None:self.ask('quit')
        self.process.stdin.close();assert self.process.wait(5)==0


@pytest.mark.parametrize('steering_available',[True,False],ids=['servo-estimate','steering-unavailable'])
def test_actual_c_initial_none_then_registered_episode_full_state(world,app_bridge_binary,tmp_path,steering_available):
    w=activate(world);provider=w.r.drive_link
    # 관측 유무는 새 plant 표본 생산에만 적용한다. 이미 만든 원 Vehicle을 고치지 않는다.
    w.sink.steering_available=steering_available
    # The software bench installs the actual generated asset identity, not the
    # transport-only ab*32 placeholder from OperatorWorld's old typed fixture.
    manifest=json.loads((Path(__file__).resolve().parents[4]/
        'platform/apps/drive/planner/generated/primitive-manifest.json').read_text())
    provider.trust.primitive=manifest['asset_sha256']
    record=w.o.missions.start(w.call('auto.resume'),1,admitted=True)
    journal(w,dict(current_mission=record));assert w.o.missions.publish()
    host_origin=time.monotonic();pi_origin=w.w.t
    def current_pi_clock():return pi_origin+time.monotonic()-host_origin
    # During real C/actor I/O, CPU work and OutputOwner waiting also consume E;
    # the pre-existing fixture clock must not freeze between pipe turns.
    w.r._clock=current_pi_clock;w.w.m.clock=current_pi_clock;w.io.clock=current_pi_clock
    plant_sequence=0;calculation=0;seen=[];confirmed=[];first_state=None;first_request=None;admitted_phase=None;selected_drive=None
    negative_observation=None
    with (tmp_path/'actual-c.stderr').open('wb') as errors:
        app=App(app_bridge_binary,errors)
        queue=[]
        def clock():
            w.w.t=pi_origin+time.monotonic()-host_origin;w.w.bind()
        def command(line):
            nonlocal selected_drive
            clock()
            rc,guest,output=app.ask(line)
            clock()
            for value in output:
                if value.startswith('WIRE '):
                    _,topic,raw=value.split(' ',2);queue.append((topic,bytes.fromhex(raw)))
                elif value.startswith('COMMAND '):
                    original=value.split(' ',2)[2];body=json.loads(original);seen.append(('command',body))
                    if admitted_phase is not None and selected_drive is None and body['command_kind']=='DRIVE':
                        selected_drive=(body,original)
                elif value.startswith('LIFECYCLE '):seen.append(('lifecycle',json.loads(value.split(' ',1)[1])))
                elif value.startswith('META '):seen.append(('meta',json.loads(value.split(' ',1)[1])))
            return rc,guest
        def refresh():
            nonlocal plant_sequence
            clock();w.record_meter()
            sample=schema.VehicleSample(major=1,**{**w.view(),'gateway_instance':provider.instance})
            assert sample.HasField('estimated_steering_rad') is steering_available
            assert not sample.HasField('measured_steering_rad')
            command('vehicle '+encode(sample).hex())
            if not plant_sequence or plant_sequence%5==0:
                value=modern_world();trust=provider.trust
                for key in ('vehicle','mapper_boot','mapper_instance','map_epoch','frame_epoch','odom_epoch',
                        'configuration','calibration','primitive','clock_epoch'):setattr(value,key,getattr(trust,key))
                value.sequence=value.observation_seq=plant_sequence+1
                # Geometry/count가 같은 stationary plant의 basis revision은 그대로다.
                # 관측/World 순번 증가를 실제 reference append로 가장하지 않는다.
                value.reference_odom_seq=w.sink.vehicle.sequence
                value.observed_us=w.o.now-trust.upper_offset_us;value.completed_us=value.observed_us+100
                value.published_us=value.observed_us+200;value.localization.observed_mapper_us=value.observed_us
                obstacle(value,14,17,10,14)
                command('world '+encode(value).hex())
            plant_sequence+=1
        def exchange():
            nonlocal first_state,first_request,admitted_phase
            while queue:
                clock()
                topic,raw=queue.pop(0);body=decode(raw,schema.DriveLink);kind=body.WhichOneof('payload')
                prepared=(w.io.prepare_snapshot(body) if kind=='snapshot' else
                    episode_request_body(raw) if kind=='episode_request' else budget_request_body(raw) if kind=='budget_request' else None)
                received=provider.receive(w.peer[0],body,w.w.t,prepared)
                seen.append(('actor',kind,received,dict(provider.reasons)))
                if received and kind=='probe' and body.probe.HasField('receipt'):
                    receipt=body.probe.receipt
                    lease=w.o.clocks.lease(w.peer,receipt.probe_sequence,w.o.now,w.w.m.generation)
                    original=w.o.clocks.sent.get((*w.peer,receipt.probe_sequence))
                    assert lease is not None and original is not None
                    assert lease.digest==receipt.reply_sha256==sha256(original.raw).digest()
                    confirmed.append((receipt.probe_sequence,receipt.reply_sha256.hex()))
                    seen.append(('clock-confirmed',*confirmed[-1]))
                if kind=='snapshot' and received:
                    if body.snapshot.episode.core.phase==0 and first_state is None:first_state=raw
                    elif body.snapshot.episode.core.state==2 and admitted_phase is None:admitted_phase=raw
                if kind=='episode_request' and first_request is None:first_request=raw
            with w.io.lock:
                outgoing=[(key,w.io.pending.pop(key)) for key in tuple(w.io.pending) if key[0]==inbound(w.peer[0])]
            for key,value in outgoing:
                clock()
                sent_clock=None
                if isinstance(value,OwnedPreparedClock):
                    wire=value.stamp(w.o.now)
                    parent=decode(wire,schema.DriveLink).clock.vehicle
                    assert parent.HasField('estimated_steering_rad') is steering_available
                    assert not parent.HasField('measured_steering_rad')
                    if steering_available:assert parent.steering_error_rad>0
                    sent_clock=SentClock(value.peer,value.generation,wire)
                else:wire=value
                command('link '+wire.hex())
                if sent_clock is not None:
                    # Like DriveLinkIo: stamp immediately before the transport,
                    # then notify the actor after the original bytes were sent.
                    # Actor work between P2 and the pipe inflated clock width.
                    clock();w.io.clock_completions.put_nowait(sent_clock);provider.tick()
        try:
            binding=w.r.bindings.current(w.peer[0],w.w.t)
            # The envelope below uses the exact C bootstrap/binding identity.
            # A different boot/app source must not be re-labelled as this producer.
            assert command('boot '+' '.join((*w.peer,w.r.authority.host,binding['boot_id'],provider.instance)))[0]==0
            control=json.dumps(w.w.m.control_payload('b08-software-bench'),separators=(',',':')).encode()
            assert command('control '+control.hex())[0]==0
            end=time.monotonic()+12
            while time.monotonic()<end and selected_drive is None:
                # One real wall clock drives both processes. No source time/E is
                # assigned to skip a failed clock or slow real planner result.
                refresh();command('step 32');exchange()
                calculation+=1;command('calc '+str(calculation));command('io');exchange()
                if not steering_available and calculation%4==0:
                    command('meta')
                    observed=next(row[1] for row in reversed(seen) if row[0]=='meta')
                    # Record the veto while its original input preconditions coexist.
                    # A later diagnostic can outlive facts; it must not replace this
                    # actual observation or refresh any producer's original E.
                    if (observed['clock_rc']==0 and observed['clock_acknowledged']
                            and observed['clock_probe_sequence']>0 and observed['maps_published']>0
                            and observed['map_current']>=0 and observed['has_grant']
                            and observed['facts_current'] and observed['search_reason_name']=='INPUT_INVALID'):
                        negative_observation=dict(observed)
                        break
                time.sleep(.003)
            command('meta');command('life')
            (tmp_path/'actual-c.transcript.json').write_text(json.dumps(app.transcript,indent=2))
            (tmp_path/'actor-observations.json').write_text(json.dumps(seen,indent=2))
            assert confirmed and all(sequence>0 for sequence,_ in confirmed),seen[-12:]
            if not steering_available:
                assert negative_observation is not None,seen[-12:]
                meta=negative_observation
                (tmp_path/'actual-missing-steering-veto.json').write_text(json.dumps(meta,indent=2))
                assert meta['clock_rc']==0 and meta['clock_acknowledged'] and meta['clock_probe_sequence']>0
                assert meta['maps_published']>0 and meta['map_current']>=0 and meta['has_grant'] and meta['facts_current']
                assert meta['search_reason_name']=='INPUT_INVALID',meta
                assert not meta['prepared'] and meta['path_current']<0
                assert first_state is first_request is admitted_phase is None
                assert w.o.episodes.status(w.peer).commit_state==1
                commands=[row[1] for row in seen if row[0]=='command']
                assert commands and all(row['command_kind']=='STOP' and row['v_mps']==row['steer_rad']==0 for row in commands)
                return
            assert first_state is not None,seen[-12:]
            assert first_request is not None,seen[-12:]
            assert admitted_phase is not None,seen[-12:]
            initial=decode(first_state,schema.DriveLink).snapshot
            request=decode(first_request,schema.DriveLink).episode_request
            active=decode(admitted_phase,schema.DriveLink).snapshot
            assert not initial.direction and not initial.task_reference.changing
            assert request.action==1 and w.o.episodes.registry['state'] is not None
            assert request.candidate.episode_id==active.episode.core.episode_id
            assert active.episode.acceptance.request_sha256==sha256(episode_request_body(first_request)).digest()
            assert active.calculation_sequence>request.calculation_sequence
            assert w.o.episodes.status(w.peer).commit_state==2
            # The actual C produced these bytes; neither app READY nor command
            # count is used to fabricate the registry/fullState condition.
            assert any(row[0]=='command' for row in seen)
            assert any(row[0]=='command' and int(row[1]['clock_probe_sequence'])>0 for row in seen)
            assert selected_drive is not None,seen[-12:]
            body,original=selected_drive
            (tmp_path/'selected-drive-command3.json').write_text(original)
            (tmp_path/'actual-drive-candidate.json').write_text(json.dumps(dict(command=body,
                bootstrap=dict(vm=w.peer[0],execution=w.peer[1],app_instance=w.peer[2],boot=binding['boot_id']),
                first_accepted_state_calculation=active.calculation_sequence,
                observed_pi_us=w.o.now,generation=w.w.m.generation,mode=w.w.m.mode.value,
                ready=w.w.m._ready(w.peer[0],'autonomous',w.w.t)),indent=2))
            assert int(body['calculation_sequence'])>active.calculation_sequence
            assert body['mission_id']==w.o.missions.current()['id']
            assert int(body['mission_revision'])==active.mission.mission_revision
            assert int(body['grant_revision'])==active.mission.grant_revision
            assert int(body['plan_revision'])==active.path_revision and int(body['phase_revision'])==active.maneuver_revision
            assert int(body['clock_probe_sequence'])>0 and int(body['valid_until_pi_us'])>w.o.now
            assert body['profile']=='drive-command-3' and body['producer_role']=='AUTO' and body['v_mps']>0
            # Same actual command ingress and one OutputOwner as the earlier
            # software boundary. This stationary plant records submissions;
            # no serial receipt or physical APPLIED is invented here.
            device=SnapshotDevice(w.sink);owner=OutputOwner(device,command_timeout_s=.1,clock=current_pi_clock)
            previous=w.r._sink;w.r._sink=owner
            before=len(w.sink.frames)
            try:
                clock();before_ready=w.w.m._ready(w.peer[0],'autonomous',w.w.t)
                message=receive_original(w,w.peer[0],body,1,emit=True)
                diagnostic=ingress_diagnostics(w,w.peer[0],owner)
                diagnostic.update(before_ready=before_ready,actual_c_boot=binding['boot_id'],
                    original_calculation_sequence=body['calculation_sequence'],original_valid_until_pi_us=body['valid_until_pi_us'],
                    first_accepted_state_calculation=active.calculation_sequence,
                    source_scope='actual C bytes; typed stationary device submission only; no physical APPLIED')
                (tmp_path/'actual-drive-ingress.json').write_text(json.dumps(diagnostic,indent=2,default=str))
                key=(w.peer[0],'autonomous',w.peer[1],w.peer[2],body['generation'])
                assert w.o.command_hwm.get(key)==int(body['calculation_sequence']),diagnostic
                frames=[frame for _,frame,_ in list(w.sink.frames)[before:]
                    if isinstance(frame,DriveFrame) and frame.witness]
                assert frames,diagnostic
                frame=frames[-1]
                assert frame.witness['message_id']==message.envelope['message_id']
                assert frame.witness['command']==json.loads(original)
                assert frame.witness['execution_id']==w.peer[1] and frame.witness['agent_instance_id']==w.peer[2]
                assert frame.expires_pi_us<=int(body['valid_until_pi_us'])
                assert all(row.get('device_applied') is not True for row in w.r.submissions)
            finally:owner.close();w.r._sink=previous
        finally:
            try:app.close()
            finally:
                (tmp_path/'actual-c.transcript.json').write_text(json.dumps(app.transcript,indent=2))
                (tmp_path/'actor-observations.json').write_text(json.dumps(seen,indent=2))
                w.io.pending.clear()
