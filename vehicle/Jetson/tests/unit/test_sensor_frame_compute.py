"""실제 admitted snapshot과 compute 제어 수명의 결합 회귀다.

센서·Clock은 기존 명시 software fixture, processor는 계산 경계를 가진 대역이다.
실제 NN/카메라/물리 FREE·지연 수락을 주장하지 않는다. 작은 예산 주입은8MiB
소유 회계의 경계를 검사하며 payload 크기·snapshot proof를 위조하지 않는다.
"""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import threading
import unittest
import weakref
from unittest.mock import patch

import test_mapper_compute as compute_fixtures
import test_mapper_sensor_state as sensor_fixtures
from xenova_drive_world import decode,encode,schema
from xenova_vehicle.runtime import mapper_compute as module
from xenova_vehicle.runtime.mapper_compute import MapperCompute,MapperComputeError,MapperInput
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.storage.contracts import canonical


class FrameFixture:
    """원 pose 증거는 그대로 두고 control 시험의 owned frame만 선언한다."""
    def __init__(self):
        self.f=sensor_fixtures.MapperSensorStateTests(
            'test_full_capture_uses_original_vehicle_anchor_interval_and_callback_metadata')
        self.f.setUp()
        self.now=self.f.now
        self.context=canonical(self.f.snapshot())
        self.f.state.ingest(self.context,now_us=self.now)
        self.generation=self.f.raw.sha256
        self.clock=compute_fixtures.Clock(self.now)

    def original(self,number,*,nn=False,generation=None,context=None,expires=None):
        source=compute_fixtures.sample(number,nn)
        rgb=replace(source.rgb,received_j_us=self.f.now)
        depth=replace(source.depth,received_j_us=self.f.now)
        original=None if source.nn_input is None else replace(source.nn_input,received_j_us=self.f.now)
        packet=None if source.nn_output is None else replace(source.nn_output,received_j_us=self.f.now)
        clocks={name:dict(bridge_sample=dict(j_before_us=str(self.f.now),sdk_us='900',j_after_us=str(self.f.now)),
            sdk_host_timestamp_j_bracket_us=['1000000','1000000']) for name in source.clocks}
        return MapperInput(compute_fixtures.identifier(1000+number),self.generation if generation is None else generation,
            CaptureSample(rgb,depth,original,packet,clocks),self.f.now+200000 if expires is None else expires,
            context_bytes=self.context if context is None else context)

    def snapshot(self,original,**changes):
        args=dict(now_us=self.clock(),expires_us=original.expires_us,job_id=original.job_id,
            source_generation=original.source_generation,source_key=original.source_key)
        args.update(changes)
        stamp=replace(self.f.stamp,sequence=original.sample.rgb.sequence)
        return self.f.state.frame_snapshot(stamp,**args)

    def bound(self,number,*,nn=False,generation=None):
        original=self.original(number,nn=nn,generation=generation)
        return original.with_sensor_snapshot(self.snapshot(original),now_us=self.clock())

    def revoke_and_renew(self):
        f=self.f;value=f.snapshot()
        probe=decode(f.probe_raw,schema.MapperClockProbe);probe.ClearField('mapper_trust')
        raw=encode(probe);key=sha256(raw).hexdigest()
        value['probes']={key:f.row(raw)};value['active_probe_sha256']=key
        f.state.ingest(canonical(value),now_us=f.now+1)
        f.state.ingest(self.context,now_us=f.now+2)
        self.clock.set(f.now+2)
        return f.state.fence_revision


class TrackingProcessor(compute_fixtures.Processor):
    def __init__(self,**kwargs):
        super().__init__(**kwargs)
        self.accepted_jobs=['old-accepted-map']
        self.cancelled_jobs=[]
        self.fences=[]
        self.fence_seen=threading.Event()

    def accepted(self,item):self.accepted_jobs.append(item.job_id)

    def cancelled(self,item,reason):self.cancelled_jobs.append((item.job_id,reason))

    def admission_fenced(self,revision,reason):
        self.fences.append((revision,reason,threading.get_ident()))
        if revision:self.fence_seen.set()


def event(owner,kind,seen,job=None):
    def found():
        seen.extend(owner.poll())
        return any(value.kind==kind and (job is None or value.job_id==job) for value in seen)
    compute_fixtures.wait_until(found)
    return next(value for value in reversed(seen) if value.kind==kind and (job is None or value.job_id==job))


class SensorFrameComputeTests(unittest.TestCase):
    def cleanup(self,owner,*gates):
        owner.request_stop()
        for gate in gates:gate.set()
        owner.join(2)
        self.assertFalse(owner.is_alive)
        self.assertIsNone(owner.terminal_error)

    def test_snapshot_binding_keeps_original_frame_bytes_and_detaches_mutable_golden(self):
        f=FrameFixture();original=f.original(1,nn=True);snapshot=f.snapshot(original)
        attached=original.with_sensor_snapshot(snapshot,now_us=f.clock())
        self.assertIsNone(original.sensor_snapshot);self.assertIs(attached.sensor_snapshot,snapshot)
        self.assertIs(attached.sample.rgb,original.sample.rgb)
        self.assertIs(attached.sample.depth,original.sample.depth)
        self.assertIs(attached.sample.rgb.payload,original.sample.rgb.payload)
        self.assertIs(attached.sample.nn_output.payload,original.sample.nn_output.payload)
        self.assertIsNot(attached.sample.nn_output,original.sample.nn_output)
        original.sample.nn_output.golden['match']=False
        self.assertTrue(attached.sample.nn_output.golden['match'])
        attached.sample.nn_output.golden['attached-only']=True
        self.assertNotIn('attached-only',original.sample.nn_output.golden)
        detached=attached.sample;detached.clocks['rgb_video']['bridge_sample']['sdk_us']='1'
        self.assertEqual(attached.sample.clocks['rgb_video']['bridge_sample']['sdk_us'],'900')
        self.assertEqual(attached.byte_length,original.byte_length+snapshot.byte_length)
        self.assertIs(attached.with_sensor_snapshot(snapshot,now_us=f.clock()),attached)
        self.assertEqual(attached.expires_us,original.expires_us)
        for changes in (dict(job_id=compute_fixtures.identifier(9999)),dict(source_generation='foreign'),
                dict(source_key=('rgb_video',999,800,900)),dict(expires_us=original.expires_us+1)):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                original.with_sensor_snapshot(f.snapshot(original,**changes),now_us=f.clock())
        changed_context=deepcopy(f.f.snapshot());changed_context['origin']['gateway_instance']=compute_fixtures.identifier(900)
        foreign=f.original(1,nn=True,context=canonical(changed_context))
        with self.assertRaises(ValueError):foreign.with_sensor_snapshot(snapshot,now_us=f.clock())
        with self.assertRaises(ValueError):original.with_sensor_snapshot(object(),now_us=f.clock())
        with self.assertRaises(ValueError):attached.with_sensor_snapshot(f.snapshot(original),now_us=f.clock())
        with self.assertRaises(ValueError):original.with_sensor_snapshot(snapshot,now_us=original.expires_us)
        # 불변 DTO의 손상 경계만 주입한다. 이를 factory가 수락한 유효 proof라고 주장하지 않는다.
        for length in (-1,True,512*1024+1):
            malformed=f.snapshot(original)
            object.__setattr__(malformed,'byte_length',length)
            with self.subTest(length=length),self.assertRaises(ValueError):
                original.with_sensor_snapshot(malformed,now_us=f.clock())

    def test_owned_budget_reserves_result_and_has_only_active_raw_nn_slots(self):
        f=FrameFixture();processor=TrackingProcessor();owner=MapperCompute(lambda _:processor,
            source_generation=f.generation,clock=f.clock)
        active=f.bound(1,nn=True);raw=f.bound(2);nn=f.bound(3,nn=True);latest=f.bound(4)
        self.assertEqual(module.MAX_OWNED_BYTES,8*1024*1024)
        self.assertEqual(module.MAX_RESULT_BYTES,2*1024*1024)
        limit=module.MAX_RESULT_BYTES+active.byte_length+raw.byte_length+nn.byte_length
        owner.start();seen=[]
        try:
            self.assertTrue(owner.submit(active));event(owner,'PARSER_START_REQUIRED',seen)
            self.assertTrue(owner.submit(raw))
            with patch.object(module,'MAX_OWNED_BYTES',limit-1):
                self.assertFalse(owner.submit(nn))
            with patch.object(module,'MAX_OWNED_BYTES',limit):
                self.assertTrue(owner.submit(nn))
            state=owner.snapshot()
            self.assertEqual((state['active'],state['pending_raw'],state['pending_nn']),
                (active.job_id,True,True))
            self.assertEqual(state['counts']['owned_budget_rejected'],1)
            self.assertTrue(owner.submit(latest))
            state=owner.snapshot()
            self.assertEqual((state['active'],state['pending_raw'],state['pending_nn']),
                (active.job_id,True,True))
            self.assertEqual(state['counts']['pending_replaced'],1)
            self.assertEqual(processor.geometries,[])
        finally:self.cleanup(owner)

    def test_fence_during_parser_keeps_nn_phases_factory_and_old_accepted_map(self):
        f=FrameFixture();entered=threading.Event();gate=threading.Event();factories=[]
        def parse(item,checkpoint):
            entered.set()
            if not gate.wait(2):raise AssertionError('bounded parser test gate expired')
            checkpoint()
            return item.sample.nn_output
        processor=TrackingProcessor(parse=parse)
        def factory(generation):factories.append(generation);return processor
        owner=MapperCompute(factory,source_generation=f.generation,clock=f.clock)
        seen=[];owner.start()
        try:
            warm=f.bound(10);owner.submit(warm);event(owner,'GEOMETRY_COMPLETED',seen,warm.job_id)
            compute_fixtures.wait_until(lambda:owner.snapshot()['active'] is None)
            accepted=list(processor.accepted_jobs)
            active=f.bound(20,nn=True);pending=f.bound(30,nn=True)
            owner.submit(active);start=event(owner,'PARSER_START_REQUIRED',seen,active.job_id)
            owner.acknowledge_parser_start(active.job_id,compute_fixtures.sent(start))
            self.assertTrue(entered.wait(2))
            owner.submit(f.bound(25));owner.submit(pending)
            revision=f.revoke_and_renew()
            self.assertEqual(revision,1)
            self.assertTrue(owner.fence_admission(revision,reason='PI_J_CLOCK_UNAVAILABLE'))
            self.assertFalse(owner.fence_admission(revision))
            with self.assertRaises(MapperComputeError):owner.fence_admission(0)
            self.assertFalse(owner.snapshot()['pending_raw']);self.assertTrue(owner.snapshot()['pending_nn'])
            self.assertIsNone(owner.take_result())
            gate.set()
            nn_done=event(owner,'NN_COMPLETED',seen,active.job_id)
            cancelled=event(owner,'CANCELLED',seen,active.job_id)
            self.assertTrue(nn_done.accepted);self.assertEqual(cancelled.reason,'ADMISSION_FENCE')
            self.assertLess(seen.index(nn_done),seen.index(cancelled))
            next_start=event(owner,'PARSER_START_REQUIRED',seen,pending.job_id)
            owner.acknowledge_parser_start(pending.job_id,compute_fixtures.sent(next_start))
            self.assertTrue(event(owner,'NN_COMPLETED',seen,pending.job_id).accepted)
            event(owner,'CANCELLED',seen,pending.job_id)
            self.assertEqual(processor.parsed,[active.job_id,pending.job_id])
            self.assertEqual(processor.accepted_jobs,accepted)
            fresh=f.bound(40);owner.submit(fresh)
            self.assertTrue(event(owner,'GEOMETRY_COMPLETED',seen,fresh.job_id).accepted)
            self.assertEqual(processor.geometries,[warm.job_id,fresh.job_id])
            self.assertEqual(processor.accepted_jobs,accepted+[fresh.job_id])
            self.assertEqual(factories,[f.generation])
            self.assertEqual([value[0] for value in processor.fences],[0,revision])
        finally:self.cleanup(owner,gate)

    def test_idle_fence_hook_runs_before_maintenance_without_recreating_processor(self):
        # maintenance가 시작된 뒤 main이 더 새 admission 입력을 선택하는 실제 경합이다.
        race=FrameFixture();armed=threading.Event();entered=threading.Event();gate=threading.Event()
        processor=TrackingProcessor()
        def require_current(original,parsed,checkpoint):
            original.sensor_snapshot.validate_frame(job_id=original.job_id,
                source_generation=original.source_generation,source_key=original.source_key,
                expires_us=original.expires_us,fence_revision=processor.fences[-1][0])
            checkpoint();return original.job_id.encode('ascii')
        processor.geometry_function=require_current
        def pause_maintenance():
            if armed.is_set():
                entered.set()
                if not gate.wait(2):raise AssertionError('bounded maintenance race gate expired')
        racing=MapperCompute(lambda _:processor,source_generation=race.generation,
            clock=race.clock,maintenance=pause_maintenance)
        racing.start();race_events=[]
        try:
            warm=race.bound(1);racing.submit(warm);event(racing,'GEOMETRY_COMPLETED',race_events,warm.job_id)
            compute_fixtures.wait_until(lambda:racing.snapshot()['active'] is None)
            armed.set();self.assertTrue(entered.wait(2))
            revision=race.revoke_and_renew();racing.fence_admission(revision,reason='VEHICLE_UNAVAILABLE')
            fresh=race.bound(2);self.assertTrue(racing.submit(fresh));gate.set()
            completed=event(racing,'GEOMETRY_COMPLETED',race_events,fresh.job_id)
            self.assertTrue(completed.accepted)
            self.assertEqual(processor.fences[-1][0],revision)
            self.assertEqual(processor.accepted_jobs,['old-accepted-map',warm.job_id,fresh.job_id])
        finally:self.cleanup(racing,gate)
        f=FrameFixture();first=TrackingProcessor();second=TrackingProcessor();factories=[];maintenance=[]
        maintained=threading.Event()
        def factory(generation):
            value=first if not factories else second
            factories.append(generation)
            return value
        def maintain():
            if factories and first.fences:
                maintenance.append((first.fences[-1][0],threading.get_ident()))
                if first.fences[-1][0]==1:maintained.set()
        owner=MapperCompute(factory,source_generation=f.generation,clock=f.clock,maintenance=maintain)
        seen=[];owner.start()
        try:
            original=f.bound(1);owner.submit(original);event(owner,'GEOMETRY_COMPLETED',seen)
            compute_fixtures.wait_until(lambda:owner.snapshot()['active'] is None)
            accepted=list(first.accepted_jobs);main=threading.get_ident()
            owner.fence_admission(f.revoke_and_renew(),reason='VEHICLE_UNAVAILABLE')
            self.assertTrue(first.fence_seen.wait(2));self.assertTrue(maintained.wait(2))
            self.assertEqual(first.accepted_jobs,accepted)
            self.assertEqual(factories,[f.generation]);self.assertEqual(first.fences[-1][:2],(1,'VEHICLE_UNAVAILABLE'))
            self.assertNotEqual(first.fences[-1][2],main)
            self.assertTrue(all(thread!=main for _,thread in maintenance))
            owner.replace_source('new-explicit-source')
            newer=f.bound(2,generation='new-explicit-source')
            # 새 source owner의 control fence는0이다. geometry 내용이나 새 source 자격은 시험하지 않는다.
            object_snapshot=newer.sensor_snapshot
            self.assertEqual(object_snapshot.fence_revision,1)
            owner.submit(newer)
            event(owner,'CANCELLED',seen,newer.job_id)
            self.assertEqual([value[0] for value in first.fences],[0,1])
            self.assertEqual([value[0] for value in second.fences],[0])
            self.assertEqual(factories,[f.generation,'new-explicit-source'])
        finally:self.cleanup(owner)

    def test_completed_result_and_terminal_geometry_are_revoked_but_nn_fact_is_kept(self):
        f=FrameFixture();processor=TrackingProcessor();owner=MapperCompute(lambda _:processor,
            source_generation=f.generation,clock=f.clock)
        owner.start();seen=[]
        try:
            original=f.bound(1,nn=True);owner.submit(original)
            start=event(owner,'PARSER_START_REQUIRED',seen)
            owner.acknowledge_parser_start(original.job_id,compute_fixtures.sent(start))
            compute_fixtures.wait_until(lambda:owner.snapshot()['result_pending'] and owner.snapshot()['active'] is None)
            original_ref=weakref.ref(original)
            original=None
            compute_fixtures.wait_until(lambda:original_ref() is None)
            accepted=list(processor.accepted_jobs)
            owner.fence_admission(f.revoke_and_renew(),reason='SENSOR_ADMISSION_REVOKED')
            self.assertIsNone(owner.take_result())
            events=owner.poll()
            nn=next(value for value in events if value.kind=='NN_COMPLETED')
            geometry=next(value for value in events if value.kind=='GEOMETRY_COMPLETED')
            self.assertTrue(nn.accepted);self.assertFalse(geometry.accepted)
            self.assertEqual(geometry.reason,'ADMISSION_FENCE')
            self.assertEqual(processor.accepted_jobs,accepted)
            self.assertTrue(processor.fence_seen.wait(2))
        finally:self.cleanup(owner)

    def test_fence_between_final_adoption_and_terminal_enqueue_cannot_emit_accepted_geometry(self):
        f=FrameFixture();processor=TrackingProcessor();owner=MapperCompute(lambda _:processor,
            source_generation=f.generation,clock=f.clock)
        entered=threading.Event();gate=threading.Event();original_emit=owner._emit;serialized=[]
        def delayed_terminal(value):
            if value.kind=='GEOMETRY_COMPLETED':
                serialized.append(owner._condition._is_owned())
                entered.set()
                if not gate.wait(2):raise AssertionError('bounded terminal test gate expired')
            return original_emit(value)
        owner._emit=delayed_terminal
        owner.start();seen=[];release_requested=threading.Event();releaser=None
        try:
            original=f.bound(1);owner.submit(original);self.assertTrue(entered.wait(2))
            self.assertEqual(processor.accepted_jobs,['old-accepted-map',original.job_id])
            if serialized[0]:
                # 채택/terminal이 같은 lock이면 main의 fence는 그 원자 단계 뒤에 선형화된다.
                # helper는 제어 API를 호출하지 않고 test gate만 해제한다.
                def release():
                    if not release_requested.wait(2):raise AssertionError('bounded main-fence test gate expired')
                    gate.set()
                releaser=threading.Thread(target=release,daemon=False)
                releaser.start();release_requested.set()
            owner.fence_admission(f.revoke_and_renew(),reason='SENSOR_ADMISSION_REVOKED')
            self.assertIsNone(owner.take_result())
            gate.set()
            terminal=event(owner,'GEOMETRY_COMPLETED',seen,original.job_id)
            self.assertFalse(terminal.accepted);self.assertEqual(terminal.reason,'ADMISSION_FENCE')
            self.assertEqual(processor.accepted_jobs,['old-accepted-map',original.job_id])
            self.assertTrue(processor.fence_seen.wait(2))
        finally:
            release_requested.set();gate.set()
            if releaser is not None:
                releaser.join(2);self.assertFalse(releaser.is_alive())
            self.cleanup(owner,gate)


if __name__=='__main__':unittest.main()
