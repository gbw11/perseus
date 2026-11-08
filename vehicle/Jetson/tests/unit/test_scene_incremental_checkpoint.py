"""실제 Mapping/Schedule/Collector/SQL의 증분 cutoff와 전체 LOAD를 검산한다.

센서 wire/clock과 CLEAR certificate/정합은 명시 software fixture다. 실제 영상
certificate 계산·장치 수락은 시험하지 않는다. 128tile 반복 body는4page 전달용이며
128개 공간의 물리 관측/coverage가 아니다. eligibility는 원 Scene commit/grant로만 얻는다.
"""

from base64 import b64encode
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import os
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

import test_mapper_mapping as mapping_fixture
from xenova_drive_world import decode, encode, schema
from xenova_drive_world.mapper_clock import restore_returned_probe
from xenova_vehicle.mapping.alignment import MapOdomAlignment
from xenova_vehicle.mapping.registration import RegistrationResult, PoseBox
from xenova_vehicle.perception.depth import DepthEvidence
from xenova_vehicle.perception.free_space import Certificate
from xenova_vehicle.perception.geometry import PointBounds
from xenova_vehicle.perception.pipeline import MapEvidenceUpdate, PerceptionPipeline
from xenova_vehicle.perception.tape import TapeGraph
from xenova_vehicle.runtime.map_checkpoint import CheckpointPages
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_hazard import MapperHazard
from xenova_vehicle.runtime.mapper_mapping import MappingNotReady
from xenova_vehicle.runtime.mapper_processor import ResidentMapperProcessor
from xenova_vehicle.runtime.mapper_scene import SceneCoordinator, SceneStationWitness
from xenova_vehicle.runtime.mapper_storage_schedule import MapperStorageSchedule
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.source_identity import SourcePolicy
from xenova_vehicle.runtime.scene import MapperScene, SceneError, SurveyObservation, SurveyProfile
from xenova_vehicle.storage.contracts import StorageJob, LoadRequest, canonical, json_bytes
from xenova_vehicle.storage.records import CheckpointPayload, TileVersion, CELL
from xenova_vehicle.storage.mapper_state import decode_mapper_state, encode_mapper_state


def uid(): return str(uuid4())


@unittest.skipUnless(os.name=='posix','실제 임시 D SQLite/lock은 대상 POSIX에서 검사한다')
class IncrementalSceneCheckpointTests(unittest.TestCase):
    def setUp(self):
        f=mapping_fixture.MapperMappingTests(methodName='runTest');self.addCleanup(f.doCleanups);f.setUp();self.f=f
        f.grant();self.hazard=MapperHazard()
        self.draft=self.frame(PerceptionPipeline(f.fixture.calibration,f.profile.pipeline))
        f.mapping.accept(self.draft)
        first=self.hazard.prepare(self.draft,now_us=f.now);self.assertTrue(first.ready,first.reason);self.hazard.accept(first)
        scope=f.mapping.collector.scope;source=f.fixture.fixture.source
        self.profile=SurveyProfile('d'*64,1,128*4096,5000,.1,.05,.1,50000,.1,.1,500000,'LIVE')
        self.scene=SceneCoordinator(f.context,source,self.profile,gateway_instance=f.fixture.fixture.gateway,evidence_scope=scope)
        pi_low=min(record.pi_lower for record in self.draft.frame.parents)
        grant=schema.SceneGrant(major=1,vehicle=source['vehicle'],pi_host_boot=f.context.pi_host_boot,
            gateway_instance=f.fixture.fixture.gateway,source_admission_revision=f.inputs.clock_lease.source_revision,
            mapper_device=source['device'],issued_pi_us=pi_low-10000,valid_until_pi_us=pi_low-10000+14_400_000_000,
            lifecycle_state=0,request_nonce=UUID(uid()).bytes,survey_deadline_pi_us=pi_low-10000+180_000_000,
            max_observation_stations=24,purpose=0,lifecycle_revision=1,frozen_survey_revision=0,frozen_hazard_revision=0,
            **{name:source[name] for name in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive')})
        grant.scene.CopyFrom(schema.SceneAdmissionRef(admission_id=UUID(uid()).bytes,admission_revision=1,
            survey_id=UUID(uid()).bytes,survey_revision=1,policy=1,coverage_profile_sha256=bytes.fromhex('d'*64),
            allow_restore_after_mapper_restart=True))
        self.scene.maintenance(encode(grant),f.inputs.clock_lease,now_j_us=f.now)
        initial=self.observation(self.draft,1);self.scene.scene.observe(initial,now_j_us=f.now)
        station=SceneStationWitness.from_frame(f.fixture.state,initial,f.context,self.profile,now_j_us=f.now)
        self.assertTrue(self.scene.scene.station(station,now_j_us=f.now))
        trust=decode(f.inputs.clock_lease.trust_bytes,schema.WorldTrust)
        grant.lifecycle_state=1;grant.lifecycle_revision+=1;grant.request_nonce=UUID(uid()).bytes
        grant.survey_deadline_pi_us=f.now+trust.upper_offset_us+300000
        self.scene.maintenance(encode(grant),f.inputs.clock_lease,now_j_us=f.now)
        job=StorageJob(f.context,uid(),f.now,f.now+200000,str(UUID(bytes=grant.request_nonce)))
        state=self.hazard.state_fields(f.mapping._pipeline,f.context,f.profile.sha256)
        snapshot=f.mapping.collector.snapshot(f.mapping._pipeline,job,now_us=f.now,
            subsystem_state=dict(f.mapping.catalogs.state_fields(),**state))
        refs=f.mapping.collector.current_tile_references(f.mapping._pipeline,snapshot)
        descriptor=self.scene.freeze_scene(job,current_tile_references=refs,current_hazard_revision=first.revision,now_j_us=f.now)
        self.base=f.mapping.queue_checkpoint(job,now_us=f.now,scene=descriptor,subsystem_state=state)
        self.scene.adopt_freeze(self.base,current_tile_references=refs,current_hazard_revision=first.revision,
            now_j_us=f.now,all_tiles=snapshot.full_tiles(),mapper_state=snapshot.mapper_state_ref)
        receipt=self.complete_pending()
        self.assertTrue(self.scene.complete_freeze(self.base,receipt,now_j_us=f.now))
        summary=decode(self.scene.scene.summary_bytes(now_j_us=f.now),schema.SurveySummary)
        grant.lifecycle_state=2;grant.lifecycle_revision+=1;grant.frozen_evidence_sha256=summary.evidence_sha256
        grant.frozen_survey_revision=summary.survey_revision;grant.frozen_hazard_revision=summary.hazard_revision
        self.assertTrue(self.scene.maintenance(encode(grant),f.inputs.clock_lease,now_j_us=f.now));self.grant=grant
        self.assertTrue(self.scene.scene.eligible)
        self.processor=ResidentMapperProcessor(canonical(f.fixture.calibration.to_dict()),f.fixture.raw,clock=lambda:f.now,
            mapper_profile_bytes=canonical(f.profile.to_dict()),mapping_context_port=f.port,mapping_mailbox=f.mailbox,
            source_policy=SourcePolicy(source['vehicle'],source['configuration'],source['calibration'],source['primitive'],f.fixture.calibration.sha256),
            clock_policy=f.fixture.policy,mapper_worker=f.context.mapper_worker,mapper_instance=f.context.origin_mapper_instance)
        # 실제 typed owner를 같은 compute에 조립한다. lifecycle/eligibility를 직접 만들지 않는다.
        self.processor._mapping=f.mapping;self.processor._scene=self.scene;self.processor._hazard=self.hazard
        self.base_bytes=self.base.payload.manifest;self.base_sha=self.base.payload.sha256
        self.advance()

    def frame(self,pipeline):
        f=self.f;item,capture=f.item();draft=f.mapping.prepare_frame(item,pipeline,capture,f.inputs,f.fixture.state,f.now)
        self.assertTrue(draft.ready,draft.reason)
        depth=DepthEvidence(capture.depth.stamp,f.fixture.calibration.sha256,'LIVE',
            (PointBounds((.001,.001,.1),(.002,.002,.2),True),),(),1,0,0,0,0,0,True,'EXPLICIT_TEST_HIT')
        with patch('xenova_vehicle.perception.depth.analyze_depth',return_value=depth), \
                patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=TapeGraph((),status='UNKNOWN',complete=False,calibration_qualified=True)):
            result=pipeline.process(capture,pose=f.inputs.pose,now_us=f.now,evidence_observer=draft.observer,
                localization_valid_for_us=100000,localization_position_drift_mps=.001,localization_yaw_drift_rps=.001)
        grid=pipeline.grid;bounds=grid.spec.bounds(0,0)
        certificate=Certificate('CLEAR_REGION',True,1,'EXPLICIT_SOFTWARE_ACCEPTANCE',1,1,
            (capture.depth.stamp.key,),f.fixture.calibration.sha256)
        # 시간/소유 경계를 위한 고정 certificate 전제다. 실제 ray coverage 검산 양성은 아니다.
        def applied(delta):
            draft.observer(MapEvidenceUpdate(grid,delta,capture,depth,f.inputs.pose,f.inputs.pose,(),
                certificate,bounds,f.profile.targets))
        grid.add_free(((0,0),),capture.depth.stamp,view_id='software-clear-'+str(capture.depth.stamp.sequence),
            origin_xy=(f.inputs.pose.sample.x,f.inputs.pose.sample.y),certificate_complete=certificate.free,observer=applied)
        result=replace(result,map_snapshot=grid.snapshot(f.now,clock_id=grid.clock_id))
        return f.mapping.finish_frame(draft,result,now_us=f.now)

    def observation(self,draft,number):
        raw=draft.inputs.pose
        fitted=raw
        sample=fitted.sample
        error,yaw=sample.position_error_m,sample.yaw_error_rad
        fit=RegistrationResult('BOUNDED',(sample.x,sample.y,sample.yaw),error,yaw,
            (PoseBox((sample.x-error,sample.x+error),(sample.y-error,sample.y+error),
                (sample.yaw-yaw,sample.yaw+yaw)),),number-1,1,1,True)
        alignment=MapOdomAlignment.from_match(raw,fitted,observed_j_interval=raw.captured_us,
            registration_revision=number,valid_for_us=500000,position_drift_mps=.001,yaw_drift_rps=.001)
        return SurveyObservation(draft.frame.observation.rgb.stamp,fitted,fit,number,self.hazard.snapshot.revision,'LIVE',raw,alignment)

    def complete_pending(self):
        f=self.f;f.mapping.maintenance(now_us=f.now)
        dispatch=f.mailbox.main_take(current_context_sha256=f.context.sha256,now_us=f.now)
        request=dispatch.request;receipt=f.store.commit(request.job,request.payload,current_context_sha256=f.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        f.mailbox.main_complete(dispatch,receipt,completed_us=f.now);f.mailbox.main_release(dispatch)
        f.mapping.maintenance(now_us=f.now)
        if f.mapping.pending is not None and f.mapping.pending.role=='HEAD_LOAD':
            dispatch=f.mailbox.main_take(current_context_sha256=f.context.sha256,now_us=f.now)
            loaded=f.store.load(dispatch.request,current_context_sha256=f.context.sha256)
            f.mailbox.main_complete(dispatch,loaded,completed_us=f.now);f.mailbox.main_release(dispatch)
            f.mapping.maintenance(now_us=f.now)
        self.assertTrue(f.mapping._head_ready)
        return receipt

    def read_inputs(self,at,sequence):
        f=self.f;s=f.fixture;original=s.fixture;f.now=at+50000
        trust=decode(s.probe_raw,schema.MapperClockProbe).mapper_trust
        offset=(trust.lower_offset_us+trust.upper_offset_us)//2
        probe=decode(s.probe_raw,schema.MapperClockProbe);probe.probe_seq+=1;probe.nonce=UUID(uid()).bytes
        probe.pi_send_us=at+offset;probe.mapper_trust.expires_pi_us=f.now+offset+500000
        s.probe_raw=encode(probe);s.probe_key=sha256(s.probe_raw).hexdigest()
        lease=restore_returned_probe(s.probe_raw,s.policy,original.source,original.pi_boot,f.now)
        wires=[]
        for index,delta in enumerate((-10000,10000)):
            vehicle=original.vehicle(index+3+2*(sequence-13),at+offset+delta,.001)
            vehicle.source_us+=at-1_000_000;vehicle.velocity_mps=0.
            wires.append(encode(vehicle))
        snapshot=s.snapshot();snapshot['active_probe_sha256']=s.probe_key
        snapshot['probes']={s.probe_key:s.row(s.probe_raw,received=f.now-3000)}
        snapshot['vehicles']=[s.row(wire,received=f.now-2000+index) for index,wire in enumerate(wires)];snapshot['tof']={}
        snapshot['storage_context']=f.context.fields;snapshot['context_sha256']=f.context.sha256
        stamp=replace(s.stamp,sequence=sequence,captured_us=(at-200,at+200),available_us=f.now)
        f.inputs=s.state.read(canonical(snapshot),stamp,now_us=f.now);self.assertIsNotNone(f.inputs.pose)
        f.source_bytes=canonical(snapshot)
        old_item,old_capture=f.item();sample=old_item.sample
        raws=tuple(replace(raw,sequence=sequence,device_us=raw.device_us+at-1_000_000,sdk_us=raw.sdk_us+at-1_000_000,
            received_j_us=f.now) for raw in (sample.rgb,sample.depth))
        capture=replace(old_capture,rgb=replace(old_capture.rgb,stamp=stamp),
            depth=replace(old_capture.depth,stamp=replace(stamp,stream_id=s.raw.fields['stream_instances']['depth_right'])))
        item=MapperInput(uid(),s.raw.sha256,CaptureSample(*raws,None,None,sample.clocks),f.now+150000,context_bytes=f.source_bytes)
        return lease,item,capture

    def advance(self):
        f=self.f;lease,item,capture=self.read_inputs(182_000_000,13)
        # 새 wire/clock은 위에서 생성한 원본만 사용한다.
        with patch.object(f,'item',return_value=(item,capture)):
            draft=self.frame(deepcopy(f.mapping._pipeline))
        self.assertTrue(draft.ready,draft.reason);f.mapping.accept(draft)
        candidate=self.hazard.prepare(draft,now_us=f.now);self.assertTrue(candidate.ready,candidate.reason)
        self.assertFalse(candidate.changed);self.hazard.accept(candidate)
        self.scene.scene.renew_clock(lease,now_j_us=f.now)
        self.assertTrue(self.scene.scene.observe(self.observation(draft,2),now_j_us=f.now))
        self.draft=draft
        # 오래된 read-job을 갱신하지 않는다. 실제 새 LOAD로 현재 D head를 관측한다.
        f.mapping.queue_head_load(StorageJob(f.context,uid(),f.now,f.now+500000))
        f.mapping.maintenance(now_us=f.now)
        dispatch=f.mailbox.main_take(current_context_sha256=f.context.sha256,now_us=f.now)
        loaded=f.store.load(dispatch.request,current_context_sha256=f.context.sha256)
        f.mailbox.main_complete(dispatch,loaded,completed_us=f.now);f.mailbox.main_release(dispatch)
        f.mapping.maintenance(now_us=f.now)

    def due(self,arguments=None):
        f=self.f;schedule=MapperStorageSchedule(f.mapping,interval_us=1,
            checkpoint_arguments=self.processor._scene_checkpoint_arguments if arguments is None else arguments)
        self.assertEqual(schedule.step(f.mapping.maintenance(now_us=f.now),now_us=f.now),'DIRTY_BATCH_WAIT');f.now+=1
        self.assertEqual(schedule.step(f.mapping.maintenance(now_us=f.now),now_us=f.now),'CHECKPOINT_QUEUED')
        return f.mapping.pending.request

    def test_actual_processor_schedule_queue_uses_snapshot_cutoff_and_preserves_base(self):
        request=self.due();manifest=json_bytes(request.payload.manifest,16384);scene=manifest['scene']
        self.assertEqual(scene['collection_end_pi_us'],manifest['durable_cutoff_pi_us'])
        self.assertGreater(scene['collection_end_pi_us'],self.grant.issued_pi_us+180000000)
        self.assertEqual(scene['base_evidence_sha256'],self.base_sha)
        self.assertEqual(self.base.payload.manifest,self.base_bytes);self.assertEqual(self.base.payload.sha256,self.base_sha)
        self.assertEqual(scene['stations'],json_bytes(self.base_bytes,16384)['scene']['stations'])
        self.complete_pending()

    def test_snapshot_callback_stale_rejects_before_payload_and_geometry_only_selection_still_persists(self):
        f=self.f;arguments=self.processor._scene_checkpoint_arguments();self.assertIn('scene_at_snapshot',arguments)
        old_pipeline=f.mapping._pipeline;old_revision=f.mapping._revision
        self.scene.revoke('SOURCE',now_j_us=f.now)
        with patch.object(CheckpointPayload,'build',side_effect=AssertionError('stale callback encoded a payload')):
            with self.assertRaises(MappingNotReady):
                f.mapping.queue_checkpoint(StorageJob(f.context,uid(),f.now,f.now+100000),now_us=f.now,**arguments)
        self.assertIsNone(f.mapping.collector._draft_snapshot);self.assertIsNone(f.mapping.pending)
        self.assertIs(f.mapping._pipeline,old_pipeline);self.assertEqual(f.mapping._revision,old_revision)
        plain=self.processor._scene_checkpoint_arguments();self.assertNotIn('scene_at_snapshot',plain);self.assertNotIn('scene',plain)
        request=f.mapping.queue_checkpoint(StorageJob(f.context,uid(),f.now,f.now+100000),now_us=f.now,**plain)
        self.assertNotIn('profile',json_bytes(request.payload.manifest,16384)['scene'])
        self.assertEqual(self.base.payload.manifest,self.base_bytes)

    def test_actual_callback_rejects_static_scene_combination_and_current_hazard_change(self):
        f=self.f;arguments=self.processor._scene_checkpoint_arguments()
        with self.assertRaises(ValueError):f.mapping.queue_checkpoint(StorageJob(f.context,uid(),f.now,f.now+100000),
            now_us=f.now,scene={},**arguments)
        self.assertIsNone(f.mapping.collector._draft_snapshot)
        self.scene.hazard(self.hazard.snapshot.revision+1,now_j_us=f.now)
        with self.assertRaises(MappingNotReady):f.mapping.queue_checkpoint(StorageJob(f.context,uid(),f.now,f.now+100000),
            now_us=f.now,**arguments)
        self.assertIsNone(f.mapping.pending);self.assertIsNone(f.mapping.collector._draft_snapshot)

    def wide_complete(self,request,*,corrupt_end=None):
        """현재 source tile1+UNKNOWN127로 실제4 LOAD를 만든다. 빈 공간/새 관측을 추가하지 않는다."""
        f=self.f;original=json_bytes(request.payload.manifest,16384)
        state,pages=decode_mapper_state(original['scene'],request.payload.evidence.contexts)
        unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)*4096
        source=request.payload.tiles[0]
        tiles=(source,)+tuple(TileVersion(f.context.map_epoch,index,0,1000+index,unknown) for index in range(1,128))
        state['tile_revision_highwater']=1127
        metadata,owned_pages=encode_mapper_state(state);old={page.sha256 for page in pages}
        evidence=replace(request.payload.evidence,contexts=tuple(item for item in request.payload.evidence.contexts if item.sha256 not in old)+owned_pages)
        scene=dict(original['scene'],mapper_state=metadata)
        if corrupt_end is not None:scene['collection_end_pi_us']=corrupt_end
        selected=(source.reference,);last=None
        for start in (1,33,65,97):
            copies=tiles[start:start+32];selected+=tuple(tile.reference for tile in copies)
            job=StorageJob(f.context,uid(),f.now,f.now+500000)
            payload=CheckpointPayload.build(job,copies,evidence,tile_references=selected,scene=scene,
                durable_cutoff_pi_us=original['durable_cutoff_pi_us'])
            receipt=f.store.commit(job,payload,current_context_sha256=f.context.sha256)
            self.assertEqual(receipt.status,'COMMITTED');last=payload
        reference=canonical(f.store.latest_complete(expires_us=f.now+500000))
        loader=CheckpointPages(reference,f.context);complete=None
        for cursor in (0,32,64,96):
            load=LoadRequest(StorageJob(f.context,uid(),f.now,f.now+500000),reference,canonical(dict(kind='CHECKPOINT_PAGE',cursor=cursor)))
            complete=loader.accept(f.store.load(load,current_context_sha256=f.context.sha256),now_us=f.now+10)
        self.assertEqual(len(complete.tiles),128);self.assertEqual(len(complete.page_receipts),4)
        return complete,last

    def restore(self,complete,payload):
        f=self.f;grant=decode(encode(self.grant),schema.SceneGrant)
        grant.lifecycle_state=1;grant.lifecycle_revision+=1;grant.purpose=1;grant.request_nonce=UUID(uid()).bytes
        trust=decode(f.inputs.clock_lease.trust_bytes,schema.WorldTrust)
        grant.survey_deadline_pi_us=f.now+trust.upper_offset_us+300000
        grant.restore_checkpoint_sha256=bytes.fromhex(payload.sha256)
        mapper=MapperScene(f.context,f.fixture.fixture.source,self.profile,
            gateway_instance=f.fixture.fixture.gateway,evidence_scope=f.mapping.collector.scope)
        mapper.receive_grant(encode(grant),f.inputs.clock_lease,now_j_us=f.now)
        lease,item,capture=self.read_inputs(182_100_000,14)
        mapper.renew_clock(lease,now_j_us=f.now)
        # 실제 새 Vehicle admission/clock/pose를 쓴다. 새 정합은 고정 software 사실이다.
        frame=replace(self.draft.frame,observation=capture,clock_lease=lease,accepted_us=f.now)
        current=self.observation(replace(self.draft,inputs=f.inputs,frame=frame),3)
        mapper.observe(current,now_j_us=f.now)
        witness=SceneStationWitness.from_frame(f.fixture.state,current,f.context,self.profile,now_j_us=f.now)
        self.assertTrue(mapper.station(witness,now_j_us=f.now))
        return mapper.restore_complete(complete,current,now_j_us=f.now+10)

    def test_production_descendant_then_actual_four_page_restore_keeps_original_cutoff_and_ages(self):
        request=self.due();self.complete_pending();complete,payload=self.wide_complete(request)
        before=tuple(tile.payload for tile in complete.tiles)
        summary=decode(self.restore(complete,payload),schema.SurveySummary)
        self.assertEqual(summary.status,4);self.assertEqual(summary.evidence_sha256.hex(),payload.sha256)
        self.assertNotEqual(summary.evidence_sha256.hex(),self.base_sha)
        self.assertEqual(tuple(tile.payload for tile in complete.tiles),before)
        self.assertEqual(complete.tiles[0].payload,request.payload.tiles[0].payload)
        self.assertTrue(all(CELL.unpack_from(tile.payload)[0]==255 for tile in complete.tiles[1:]))
        self.assertEqual(self.base.payload.manifest,self.base_bytes)

    def test_future_or_nonexact_descendant_cutoff_never_becomes_restored_scene(self):
        request=self.due();self.complete_pending()
        cutoff=json_bytes(request.payload.manifest,16384)['durable_cutoff_pi_us']
        complete,payload=self.wide_complete(request,corrupt_end=cutoff+1)
        with self.assertRaisesRegex(SceneError,'cutoff'):self.restore(complete,payload)

    def test_actual_snapshot_future_parent_bound_and_concurrent_publication_change_fail_closed(self):
        f=self.f;arguments=self.processor._scene_checkpoint_arguments()
        job=StorageJob(f.context,uid(),f.now,f.now+100000)
        snapshot=f.mapping.collector.snapshot(f.mapping._pipeline,job,now_us=f.now,
            subsystem_state=dict(f.mapping.catalogs.state_fields(),**arguments['subsystem_state']))
        frame=f.mapping.collector._last
        approved=max(frame.pi_interval[1],*(parent.pi_upper for parent in frame.parents))
        with self.assertRaisesRegex(SceneError,'cutoff'):
            self.scene.scene.descendant_scene(replace(snapshot,durable_cutoff_pi_us=approved+1),frame=frame,now_j_us=f.now)
        with self.assertRaises(SceneError):
            self.scene.scene.descendant_scene(snapshot,frame=replace(frame,clock_lease=None),now_j_us=f.now)
        f.mapping.collector.discard_snapshot(snapshot)
        binding=json_bytes(f.context.source_binding,2048);binding['source_binding_revision']='2'
        changed=replace(f.context,source_binding=canonical(binding));f.port.publish(changed)
        with patch.object(CheckpointPayload,'build',side_effect=AssertionError('changed source encoded a payload')):
            with self.assertRaises(MappingNotReady):f.mapping.queue_checkpoint(job,now_us=f.now,**arguments)
        self.assertIsNone(f.mapping.collector._draft_snapshot);self.assertIsNone(f.mapping.pending)
        self.assertEqual(self.base.payload.manifest,self.base_bytes)


if __name__=='__main__':unittest.main()
