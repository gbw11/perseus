"""Actual actor/Authority/clock/Scene flow with explicit J and feedback3 fixtures.

Broker, serial feedback3 ingress and the S18 global operator/Mission issuer are not
implemented by these fixtures. POSIX fsync and the bounded journal thread are real.
"""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
import json
import os
import threading
import time

import pytest

from xenova_drive_world import decode, encode, schema
from xenova_drive_world.mapper_clock import ClockPolicy, build_probe, candidate_body
from xenova_mqtt.drive_binding import BindingCache
from xenova_gateway.authority import AuthorityStore, AuthorityError
from xenova_gateway.drive_link import DriveLinkIO, DriveLinkProvider, inbound
from xenova_gateway.machine import ForwardStop, Mode
from xenova_gateway.mapper_uplink import GatewayMapperUplink, UplinkProfile
from xenova_gateway.scene_authority import ScenePolicy, StationaryPolicy, SceneRequest, SceneAuthorityError, SceneJournalAck, MatchedStop, FEEDBACK3
from xenova_gateway.scene_runtime import SceneSubmission
from xenova_gateway.sink import MockSink
from xenova_gateway.verified_runner import VerifiedGatewayRunner
from test_scene_authority import uid, source, gate, canonical, summary
from test_verified import Client

pytestmark=pytest.mark.skipif(not hasattr(os,'O_DIRECTORY'),reason='actual POSIX Authority journal')


class LocalBoundary:
    """Only the existing J child transport boundary; no process/reap claim."""
    pending=None
    def __init__(self):self.replies=[];self.sent=[]
    def flush(self,now):pass
    def receive(self,now):values,self.replies=self.replies,[];return values
    def offer(self,*args):self.sent.append(args);return True


class Feedback3Boundary(MockSink):
    """Typed ingress fixture, distinct from the installed feedback2 producer."""
    def device_view(self):
        return dict(feedback_supported=True,ready=True,fault=None,pi_session=uid(7),
            device_session=uid(2),vehicle_sample=None,accepted=None,applied=None,physical_stop=None)


class SceneWorld:
    def __init__(self,path,*,native_profile=None):
        self.now=1_000_000;self.seq=0
        self.src=source()
        if native_profile is not None:
            self.src.update(configuration=native_profile[0],calibration=native_profile[1],primitive=native_profile[2])
        self.store=AuthorityStore(Path(path),uid(1),uid=os.geteuid(),mountpoint=None)
        self.store.initialize(uid(90))
        self.runner=VerifiedGatewayRunner(Client(),Feedback3Boundary(),authority=self.store,
            bindings=BindingCache(uid(1),clock=lambda:self.now/1e6),vm_ids=(uid(90),uid(91)),
            clock=lambda:self.now/1e6,readiness_profile='warm-drive-1')
        self.runner._app_filters=[]
        self.io=DriveLinkIO('127.0.0.1',1883,None,None,self.runner.pair,clock=lambda:self.now/1e6)
        self.runner.drive_link=DriveLinkProvider(self.runner,self.io,None,uid(4))
        clock=ClockPolicy('rc-test-1','oak-1',self.src['configuration'],self.src['calibration'],
            self.src['primitive'],2,10,5000,1_000_000,500_000,1_000_000)
        self.uplink=GatewayMapperUplink(UplinkProfile(clock,'127.0.0.1',1883,None,None),
            uid(1),uid(4),clock=lambda:self.now)
        self.uplink.link=LocalBoundary();self.uplink.process=SimpleNamespace(exitcode=None)
        self.uplink.uplink_instance=uid(13);self.uplink.channel_epoch=uid(14)
        self.runner.mapper_uplink=self.uplink
        self.runner.begin()
        self.policy=ScenePolicy('d'*64,1,4096,.05,.05,500_000,
            StationaryPolicy(10_000,30_000,1_000_000,.000001,.01,.01,0.,0.))
        self.runner.configure_scene(self.policy);self.runtime=self.runner.scene_runtime
        self.current_gate=replace(gate(),mode='HOLD')
        if native_profile is not None:
            window=[]
            for raw in self.current_gate.stop.vehicle_window:
                sample=decode(raw,schema.VehicleSample);sample.calibration=self.src['calibration'];window.append(encode(sample))
            self.current_gate=replace(self.current_gate,stop=replace(self.current_gate.stop,vehicle_window=tuple(window)))
        self.runner.update_scene_gate(self.current_gate)
        self.exchange(1_000_000);self.exchange(1_200_000)

    def close(self):self.runtime.close()

    def tick(self,at=None):
        if at is not None:self.now=at
        self.runner.run_once(0)

    def exchange(self,at,evidence=None):
        self.now=at
        self.seq=max(self.seq+1,decode(self.current_gate.stop.vehicle_window[-1],schema.VehicleSample).sequence+1)
        sample=decode(self.current_gate.stop.vehicle_window[-1],schema.VehicleSample)
        sample.sequence=self.seq;sample.source_sample=self.seq;sample.pi_sample_us=at
        sample.source_us=at-100;self.uplink.offer_vehicle(encode(sample))
        self.tick()
        plan=self.uplink.owner.pending
        assert plan is not None
        wire=build_probe(plan,at+100);probe=decode(wire,schema.MapperClockProbe)
        raw=schema.MapperClockReply(major=1,pi_host_boot=uid(1),pi_client_instance=probe.pi_client_instance,
            probe_seq=probe.probe_seq,nonce=probe.nonce,pi_send_us=probe.pi_send_us,
            j2=at+200,j3=at+220,**{k:v for k,v in self.src.items() if k!='vehicle'})
        if evidence is not None:raw.survey_summary.CopyFrom(evidence)
        header=dict(kind='TRUST_CANDIDATE',request_id=plan.request_id,
            issued_pi_us=str(at+500),expires_pi_us=str(plan.expires_us))
        self.uplink.link.replies.append((header,candidate_body(wire,encode(raw),at+500)))
        self.tick(at+500)
        return self.runtime.lease

    def request(self,action,n,at,args):
        return SceneRequest(uid(n),uid(n+100),at,at+1_000_000,1,action,canonical(args))

    def refresh_gate(self,at):
        values=[]
        for n,stamp in enumerate((at-20_000,at),100):
            sample=decode(self.current_gate.stop.vehicle_window[-1],schema.VehicleSample)
            sample.sequence=n;sample.source_sample=n;sample.pi_sample_us=stamp;sample.source_us=stamp-100
            values.append(encode(sample))
        report=list(FEEDBACK3.unpack(self.current_gate.stop.feedback_payload))
        report[7]+=1;report[8]=101;report[9]=at-100
        stop=MatchedStop(self.current_gate.stop.command_payload,FEEDBACK3.pack(*report),tuple(values))
        self.current_gate=replace(self.current_gate,stop=stop)
        self.runner.update_scene_gate(self.current_gate)

    def settle(self):
        deadline=time.monotonic()+2
        while self.runtime._submitted is not None or self.runtime.owner.take_journal() is not None:
            assert time.monotonic()<deadline,'actual Scene journal did not settle'
            self.tick();time.sleep(.001)

    def begin(self):
        self.now=1_201_000
        req=self.request('scene.survey.begin',20,self.now,dict(policy='STATIC_FLAT_SURVEY_1',allow_mapper_restore=True))
        original=SceneSubmission(req,self.current_gate,uid(30),uid(31))
        accepted=self.runner.submit_scene(original);self.tick();assert accepted.result()=='SURVEYING'
        self.settle()
        assert self.uplink.owner.source_revision==2
        assert self.runtime.lease.source_revision==1
        assert self.runtime.motion_grant(self.now) is None
        assert self.uplink.last_scene is None
        self.exchange(1_400_000)
        grant=decode(self.runtime.owner.grant_bytes,schema.SceneGrant)
        evidence=summary(grant);evidence.started_peer_us=1_210_000
        evidence.last_observed_peer_us=1_590_000;evidence.completed_peer_us=1_591_000
        self.exchange(1_600_000,evidence)
        assert self.runtime.owner._start_peer==1_210_000
        return original

    def admit(self):
        original=self.begin();self.now=1_601_000
        req=self.request('scene.survey.commit',21,self.now,dict(survey_id=uid(31)))
        accepted=self.runner.submit_scene(SceneSubmission(req,self.current_gate));self.tick()
        assert accepted.result()=='FREEZE_PENDING';self.settle()
        pending=decode(self.runtime.owner.grant_bytes,schema.SceneGrant)
        frozen=summary(pending,status=1);frozen.started_peer_us=1_210_000
        frozen.last_observed_peer_us=1_790_000;frozen.completed_peer_us=1_791_000
        self.refresh_gate(1_780_000)
        self.exchange(1_800_000,frozen);self.settle()
        assert not self.runtime.owner.eligible
        assert decode(self.runtime.owner.grant_bytes,schema.SceneGrant).lifecycle_state==2
        assert decode(self.io.pending[inbound(uid(90)),'scene'],schema.DriveLink).scene_grant.lifecycle_state!=2
        applied=schema.SurveySummary();applied.CopyFrom(frozen);applied.status=2
        self.exchange(2_000_000,applied)
        assert self.runtime.owner.eligible
        admitted=self.io.pending[inbound(uid(90)),'scene']
        assert encode(decode(admitted,schema.DriveLink).scene_grant)==self.runtime.owner.grant_bytes
        return original,admitted,applied


@pytest.fixture
def scene_world(tmp_path):
    value=SceneWorld(tmp_path/'scene-authority')
    try:yield value
    finally:value.close()


def test_actual_actor_journal_mapper_applied_and_ram_negative(scene_world):
    w=scene_world;original,admitted,applied=w.admit()
    grant=decode(admitted,schema.DriveLink).scene_grant
    assert w.store.value['current_scene']['state']=='COMMITTING'
    assert w.store.value['generation']==1 and w.runner.machine.mode is Mode.SAFE_STOP
    before=w.runtime.owner.grant_bytes;revision=w.uplink.owner.source_revision
    duplicate=w.runner.submit_scene(original);w.tick();assert duplicate.result()=='ADMITTED'
    assert w.runtime.owner.grant_bytes==before and w.uplink.owner.source_revision==revision
    invalid=schema.SurveySummary();invalid.CopyFrom(applied);invalid.status=3
    w.exchange(2_200_000,invalid)
    negative=w.io.pending[inbound(uid(90)),'scene']
    revoked=decode(negative,schema.DriveLink).scene_grant
    expected=schema.SceneGrant();expected.CopyFrom(grant)
    expected.lifecycle_state=3;expected.lifecycle_revision+=1
    assert encode(revoked)==encode(expected)
    assert not w.runtime.owner.eligible and w.uplink.owner.source_revision==revision+1
    assert w.uplink.last_scene[2]==encode(revoked)
    assert w.runtime.command_allowed({'app_id':'manual'},w.now)
    w.tick(w.now+1_000);assert w.io.pending[inbound(uid(90)),'scene']==negative
    assert w.uplink.owner.source_revision==revision+1
    w.tick(3_202_000)
    assert w.uplink.owner.trust(w.now) is None
    assert w.uplink.last_scene[2]==encode(revoked)
    assert w.io.pending[inbound(uid(90)),'scene']==negative
    assert w.uplink.owner.source_revision==revision+1


def original_mission(w,scene):
    """명시된 S18 issuer/durable-precondition fixture를 실제 transport로 전달한다."""
    now=int(w.now/1000);vm=uid(90);execution=uid(92);instance=uid(93)
    w.runner.bindings.install(dict(schema_version='1.0-drive-bindings',host_boot_id=uid(1),
        observed_monotonic_ms=now,expires_monotonic_ms=now+3000,vms={vm:dict(status='observed',
            profile='warm-drive-1',execution_id=execution,boot_id=uid(94),app_instance_id=uid(95),
            app_instances={'autonomous':instance,'manual':uid(96)},domid=1,xen_unique_id='1',
            app_state_models={'autonomous':'stateless','manual':'stateless'},intent_revision=1,
            lifecycle_generation=1,observed_monotonic_ms=now)}))
    # This is the declared original issuer/durable-precondition fixture. The
    # transport and Scene owner neither mint nor modify it; S18 owns that issuer.
    grant=schema.MissionGrant(major=1,vehicle='rc-test-1',pi_host_boot=UUID(uid(1)).bytes,
        gateway_instance=UUID(uid(4)).bytes,owner_vm=UUID(vm).bytes,owner_execution=UUID(execution).bytes,
        app_instance=UUID(instance).bytes,control_generation=1,mission_id=UUID(uid(97)).bytes,
        mission_revision=1,grant_revision=1,kind=1,strategy=0,operator_request_nonce=UUID(uid(98)).bytes,
        issued_pi_us=w.now,mission_deadline_pi_us=0,cancel_fence_revision=0,
        scene_admission_id=scene.scene.admission_id,scene_revision=scene.scene.admission_revision,
        configuration=w.src['configuration'],calibration=w.src['calibration'],primitive=bytes.fromhex(w.src['primitive']),
        execution_deadline_pi_us=0,correction_attempts_used=0,execution_travel_m=0.,
        max_execution_travel_m=0.,admitted=True,search_deadline_pi_us=0,search_travel_m=0.,max_search_travel_m=0.)
    original=encode(grant);assert w.runner.drive_link.publish_mission(grant)
    assert encode(decode(w.io.pending[inbound(vm),'grant'],schema.DriveLink).mission_grant)==original
    assert w.runtime.command_allowed({'app_id':'autonomous','vm_id':vm},w.now)
    return grant,original


def test_original_mission_scene_pair_is_consumed_without_transport_rewrite(scene_world):
    w=scene_world;_,admitted,_=w.admit();scene=decode(admitted,schema.DriveLink).scene_grant
    grant,original=original_mission(w,scene);vm=uid(90)
    w.runtime.owner._ram_fence('MAPPER_INVALID');w.tick()
    assert w.runner.drive_link.missions.current_grant(vm)==original
    assert not w.runtime.command_allowed({'app_id':'autonomous','vm_id':vm},w.now)
    assert not w.runner.drive_link.publish_mission(grant)
    assert w.runtime.command_allowed({'app_id':'manual'},w.now)


@pytest.fixture(scope='module')
def actor_scene_probe(tmp_path_factory):
    # Reuse the C owner's exact native compile fixture. Runtime dependencies and
    # product imports do not gain a tests/ path; this is a Nano cohort adapter.
    import importlib
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(Path(__file__).resolve().parents[3]/'apps/drive/tests'))
        native=importlib.import_module('test_scene_consumption_native')
        binary=native.scene_probe.__wrapped__(tmp_path_factory)
    return native,binary


@pytest.mark.parametrize('reason',['mapper-invalid','operator-revoke'])
def test_actual_actor_tag10_bytes_enter_native_c_gate(actor_scene_probe,tmp_path,reason):
    native,binary=actor_scene_probe
    profile=native.fixture.world()
    w=SceneWorld(tmp_path/'native-authority',native_profile=(profile.configuration,profile.calibration,profile.primitive))
    try:
        _,admitted,applied=w.admit();grant=decode(admitted,schema.DriveLink).scene_grant
        original_mission(w,grant)
        mission_bytes=w.io.pending[inbound(uid(90)),'grant']
        if reason=='mapper-invalid':
            invalid=schema.SurveySummary();invalid.CopyFrom(applied);invalid.status=3
            clock=w.exchange(2_200_000,invalid)
        else:
            w.now=2_200_000;req=w.request('scene.revoke',22,w.now,dict(scene_id=uid(30)))
            accepted=w.runner.submit_scene(SceneSubmission(req,None));w.tick()
            assert accepted.result()=='REVOKED';w.settle();clock=w.runtime.lease
        negative=w.io.pending[inbound(uid(90)),'scene']
        world=native.world_for_scene(grant,decode(clock.trust_bytes,schema.WorldTrust),observed_mapper_us=2_100_000)
        result=native.run_external_scene(binary,tmp_path,encode(world),admitted,negative,
            clock.trust_bytes,uid(1),uid(4),2_201_000,mission_bytes=mission_bytes)
        assert not result.stderr
    finally:w.close()


def test_original_request_expiry_and_unknown_journal_do_not_reissue(scene_world,monkeypatch):
    w=scene_world;w.now=1_201_000
    request=w.request('scene.survey.begin',20,w.now,dict(policy='STATIC_FLAT_SURVEY_1',allow_mapper_restore=True))
    original=w.store.save
    def lost_ack(value):original(value);raise OSError('explicit after-fsync lost acknowledgement')
    monkeypatch.setattr(w.store,'save',lost_ack)
    accepted=w.runner.submit_scene(SceneSubmission(request,w.current_gate,uid(30),uid(31)))
    w.tick();assert accepted.result()=='SURVEYING'
    deadline=time.monotonic()+2
    while w.runtime._submitted is not None:
        assert time.monotonic()<deadline;w.tick();time.sleep(.001)
    pending=w.runtime._unknown
    assert pending is not None and w.runtime.owner.take_journal() is pending
    assert not w.runtime.owner.eligible and w.runtime.journal_unknown==1
    for _ in range(3):w.tick()
    assert w.runtime._submitted is None and w.runtime.journal_unknown==1


@pytest.mark.parametrize('completion',['ack','failed','unknown'])
def test_pending_revoke_duplicate_and_late_real_ack_never_publish_positive(scene_world,monkeypatch,completion):
    w=scene_world;w.begin();w.now=1_601_000
    commit=w.request('scene.survey.commit',21,w.now,dict(survey_id=uid(31)))
    accepted=w.runner.submit_scene(SceneSubmission(commit,w.current_gate));w.tick()
    assert accepted.result()=='FREEZE_PENDING';w.settle()
    parent=decode(w.uplink.last_scene[2],schema.SceneGrant)
    frozen=summary(parent,status=1);frozen.started_peer_us=1_210_000
    frozen.last_observed_peer_us=1_790_000;frozen.completed_peer_us=1_791_000
    w.refresh_gate(1_780_000)
    entered=threading.Event();release=threading.Event();original=w.store.save
    original_durable=w.runtime.owner._durable
    def blocked_save(value):
        if value.get('current_scene',{}).get('state')=='COMMITTING':
            entered.set();assert release.wait(2)
            if completion=='failed':raise AuthorityError('explicit known rejection before write')
            if completion=='unknown':original(value);raise OSError('explicit after-fsync lost acknowledgement')
        else:
            pending=w.runtime.owner.take_journal()
            assert pending.request is req and pending.request.expires_us==req.expires_us
            assert pending.expected_record==(original_durable if completion=='failed' else original_proposal.record)
        return original(value)
    monkeypatch.setattr(w.store,'save',blocked_save)
    try:
        w.exchange(1_800_000,frozen);assert entered.wait(1)
        original_proposal=w.runtime._submitted
        proposed=decode(w.runtime.owner._grant,schema.SceneGrant)
        assert proposed.lifecycle_state==2 and proposed.HasField('frozen_evidence_sha256')
        w.now=1_801_000;req=w.request('scene.revoke',41,w.now,dict(scene_id=uid(30)))
        submission=SceneSubmission(req,None)
        revoked=w.runner.submit_scene(submission);w.tick();assert revoked.result()=='REVOKED'
        revision=w.uplink.owner.source_revision
        expected=schema.SceneGrant();expected.CopyFrom(parent)
        expected.lifecycle_state=3;expected.lifecycle_revision=proposed.lifecycle_revision+1
        negative=w.io.pending[inbound(uid(90)),'scene']
        assert encode(decode(negative,schema.DriveLink).scene_grant)==encode(expected)
        assert w.uplink.last_scene[2]==encode(expected)
        duplicate=w.runner.submit_scene(submission);w.tick();assert duplicate.result()=='REVOKED'
        assert w.uplink.owner.source_revision==revision
        assert not w.runtime.owner.eligible
    finally:release.set()
    if completion=='unknown':
        deadline=time.monotonic()+2
        while w.runtime._submitted is not None:
            assert time.monotonic()<deadline;w.tick();time.sleep(.001)
        assert w.runtime._unknown is original_proposal
        assert w.runtime.owner.take_journal() is original_proposal
        assert w.runtime.owner._deferred[0] is req
        assert w.runtime.owner.scene_commit_hold and w.runtime.journal_unknown==1
        duplicate=w.runner.submit_scene(submission);w.tick();assert duplicate.result()=='REVOKED'
        for _ in range(3):w.tick()
        assert w.runtime._submitted is None and w.uplink.owner.source_revision==revision
        assert w.io.pending[inbound(uid(90)),'scene']==negative
        return
    w.settle();w.tick()
    assert w.runtime.owner.state=='REVOKED' and not w.runtime.owner.eligible
    assert w.runtime.owner.grant_bytes==encode(expected)
    assert w.io.pending[inbound(uid(90)),'scene']==negative
    assert w.uplink.last_scene[2]==encode(expected)
    row=w.store.value['current_scene']['requests'][-1]
    assert (row['nonce'],row['fingerprint'],row['source_admission_revision'])==(req.nonce,req.fingerprint,revision)
    assert expected.request_nonce!=UUID(req.nonce).bytes
    assert w.runtime.owner._deferred is None and w.runtime._unknown is None
    assert w.runtime.failed==int(completion=='failed')
    with pytest.raises(SceneAuthorityError):
        w.runtime.owner.complete_journal(SceneJournalAck(original_proposal,w.now),w.current_gate,now_us=w.now)
    assert w.runtime.owner.grant_bytes==encode(expected) and not w.runtime.owner.eligible


def test_registered_calibration_bundle_keeps_exact_vehicle_binding(tmp_path):
    w=SceneWorld(tmp_path/'registered-calibration',native_profile=('scene-lab','encoder-lab-1','b'*64))
    try:
        w.begin()
        assert decode(w.runtime.owner.grant_bytes,schema.SceneGrant).calibration=='encoder-lab-1'
        window=list(w.current_gate.stop.vehicle_window)
        wrong=decode(window[-1],schema.VehicleSample);wrong.calibration='other-registered-bundle'
        window[-1]=encode(wrong)
        mismatch=replace(w.current_gate,stop=replace(w.current_gate.stop,vehicle_window=tuple(window)))
        req=w.request('scene.survey.commit',21,1_601_000,dict(survey_id=uid(31)))
        accepted=w.runner.submit_scene(SceneSubmission(req,mismatch));w.tick(1_601_000)
        with pytest.raises(SceneAuthorityError):accepted.result()
        assert w.runtime.owner.state=='SURVEYING' and not w.runtime.owner.eligible
    finally:w.close()


def test_actual_store_lock_does_not_block_actor_stop_or_original_failure(scene_world,monkeypatch):
    w=scene_world;entered=threading.Event();release=threading.Event();original=w.store.save
    def blocked_save(value):
        entered.set();assert release.wait(2);return original(value)
    monkeypatch.setattr(w.store,'save',blocked_save)
    w.now=1_201_000;req=w.request('scene.survey.begin',20,w.now,dict(policy='STATIC_FLAT_SURVEY_1',allow_mapper_restore=True))
    accepted=w.runner.submit_scene(SceneSubmission(req,w.current_gate,uid(30),uid(31)))
    try:
        w.tick();assert accepted.result()=='SURVEYING';assert entered.wait(1)
        assert not w.runner._durable_available()
        m=w.runner.machine;generation=m.generation
        m._fail('FIRST_ORIGINAL_FAILURE',1.202)
        m._fail('LATER_DIAGNOSTIC',1.203)
        assert m._deferred_authority[1]['failure']['observed_monotonic_ms']==1202
        start=time.monotonic();w.tick(1_204_000)
        assert time.monotonic()-start<.2 and w.runtime.worker.busy
        m._last_stop_t=None
        assert w.runner._sink.frames and isinstance(m._output(w.now/1e6)[0],ForwardStop)
        assert m.generation==generation
    finally:release.set()
    w.settle();w.tick()
    assert w.store.value['failure']['observed_monotonic_ms']==1202
    assert w.store.value['failure']['reason']=='FIRST_ORIGINAL_FAILURE'


def test_opt_in_policy_has_no_invented_defaults():
    clock=ClockPolicy('rc-test-1','oak-1','scene-lab','a'*64,'b'*64,2,10,5000,1_000_000,500_000,1_000_000)
    assert UplinkProfile(clock,'127.0.0.1',1883,None,None).scene_policy is None
    with pytest.raises(SceneAuthorityError):ScenePolicy.from_dict({})


@pytest.mark.parametrize('mode',['AUTO','MANUAL'])
def test_busy_guard_preserves_healthy_current_data_and_original_timeout(tmp_path,mode):
    from test_warm import WarmWorld
    from xenova_gateway.machine import ForwardDrive
    w=WarmWorld(tmp_path/'warm-authority',mode=mode)
    if mode=='MANUAL':
        # 사용자 선택 MANUAL은 저장된 hold가 필요하다. hold 없는 MANUAL은
        # 정상 AUTO 복구 때 AUTO_READY로 진행하는 기존 정책을 사용한다.
        w.auth.update(manual_hold=True)
    w.arm()
    assert w.auth.value['mode']==mode and w.m.mode.value==mode and w.m.output_permitted
    assert w.m.active_app==('manual' if mode=='MANUAL' else 'autonomous')
    entered=threading.Event();release=threading.Event()
    def lock_fault():
        with w.auth._lock:entered.set();assert release.wait(2)
    thread=threading.Thread(target=lock_fault,name='explicit-store-lock-fault')
    thread.start();assert entered.wait(1)
    w.m.durable_write_available=lambda:not thread.is_alive()
    generation=w.m.generation
    try:
        start=time.monotonic();w.tick(.04)
        assert time.monotonic()-start<.2
        assert any(isinstance(action,ForwardDrive) for action in w.actions)
        assert w.m.mode.value==mode and w.m.generation==generation
        w.alive.clear();actions=[]
        for _ in range(12):w.tick(.02);actions.extend(w.actions)
        assert any(isinstance(action,ForwardStop) for action in actions)
        assert w.m.generation==generation and w.m._authority_transition_deferred
    finally:release.set();thread.join(1)
    assert not thread.is_alive()
