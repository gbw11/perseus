"""실제 cell mutation→원 증거 copy→SQLite LOAD와 정적 복원 경계를 검산한다.

센서/운동 보정은 명시 software fixture다. 시간 주입으로 SQL 결과를 검산하며 Nano
처리율/실차 정확도/scene 사용권을 수락하지 않는다. 실제 제품 실행은 대상에서 수행한다.
"""

from dataclasses import replace
from hashlib import sha256
import os
from pathlib import Path
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID,uuid4

from xenova_drive_world import encode
from xenova_vehicle.mapping.local_map import GridSpec,GridMutation
from xenova_vehicle.mapping.odometry import PoseEstimate
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.perception.calibration import CalibrationProfile
from xenova_vehicle.perception.depth import DepthEvidence
from xenova_vehicle.perception.free_space import TargetClass
from xenova_vehicle.perception.inputs import FrameStamp,ImageFrame,PerceptionInput
from xenova_vehicle.perception.pipeline import MapEvidenceUpdate,PerceptionPipeline,PipelineProfile
from xenova_vehicle.perception.tape import TapeGraph
from xenova_vehicle.runtime.map_checkpoint import (MapCheckpointCollector,MapCheckpointNotReady,
    FrameEvidence,CheckpointPages,SourceComplete,restore_pipeline)
from xenova_vehicle.runtime.raw_binding import RawBinding
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.resident_storage import _Database,StorageProfile
from xenova_vehicle.runtime.scene import SceneEvidenceScope
from xenova_vehicle.storage.contracts import StorageContext,StorageJob,LoadRequest,canonical,json_bytes,PersistenceError
from xenova_vehicle.storage.geometry_scope import PiGeometryOrigin
from xenova_vehicle.storage.records import CELL,PROVENANCE,ContextRecord,ProvenanceRecord,EvidenceTable,TileVersion,CheckpointPayload,CatalogRecord
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.mapper_state import encode_mapper_state,decode_mapper_state
from xenova_vehicle.storage.repository import PersistenceStore

import test_depth_evidence as depth_fixture
import test_sensor_samples as sensor_fixture


def uid():return str(uuid4())


@unittest.skipUnless(os.name=='posix','실제 D writer lock/SQLite는 대상 POSIX에서 검사한다')
class MapCheckpointTests(unittest.TestCase):
    def setUp(self):
        from xenova_vehicle.transport.sensor_samples import SensorContext,SensorSamples
        self.sensor=sensor_fixture.SensorSampleTests(methodName='runTest');self.sensor.setUp()
        fields=depth_fixture.profile(6).to_dict();fields.update(kind='LIVE',vehicle_bundle_sha256='a'*64)
        self.calibration=CalibrationProfile.from_dict(fields);s=self.sensor
        self.admission=SensorSamples(SensorContext(self.calibration,s.source,pi_host_boot=s.pi_boot,
            gateway_instance=s.gateway,pi_session=s.pi_session,device_session=s.device),tof_leaves={},
            vehicle_max_age_us=200_000,tof_max_age_us=200_000,max_speed_mps=.5,max_yaw_rate_rps=2.)
        original=s.vehicle(pi_us=1_100_050);original.clock_error_us=0;original.position_error_m=0.;original.heading_error_rad=0.
        self.vehicle=self.admission.accept_vehicle(encode(original),s.lease,s.now)
        self.pose=PoseEstimate(self.vehicle.pose_sample,self.vehicle.j_interval,self.vehicle.j_interval)
        self.raw=RawBinding(dict(service_instance=uid(),acquisition_worker=uid(),pipeline_instance=uid(),capture_epoch=uid(),
            stream_instances=dict(rgb_video=uid(),depth_right=uid()),sdk_bridge_epoch=uid(),device_id='oak-fixture',
            model_id=None,parser_id=None,graph_profile_sha256='e'*64,calibration_bundle=self.calibration.sha256,
            format_table_sha256='f'*64,clock_epoch=s.source['clock_epoch']))
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.db=_Database(StorageProfile(str(Path(tmp.name)/'map.sqlite3'),'CREATE_IF_ABSENT'),time.monotonic_ns()//1000+5_000_000)
        self.addCleanup(self.db.close)
        publication=dict(service_instance=self.raw.fields['service_instance'],mapper_worker=uid(),mapper_instance=s.source['mapper_instance'],
            raw_binding_sha256=self.raw.sha256,**{key:s.source[key] for key in ('map_epoch','frame_epoch','odom_epoch','configuration','calibration','primitive')},
            scene_admission_ref=None,source_binding_revision='1')
        origin=PiGeometryOrigin(s.source['vehicle'],s.pi_boot,s.gateway,s.device,s.pi_session,s.source['odom_epoch'],'a'*64)
        self.context=StorageContext(self.db.metadata['database_uuid'],publication['mapper_worker'],s.source['mapper_instance'],
            s.source['map_epoch'],s.source['frame_epoch'],s.source['odom_epoch'],s.pi_boot,s.source['configuration'],'a'*64,'b'*64,
            canonical(publication),geometry_origin=origin.fields)
        self.scope=SceneEvidenceScope(self.context,self.raw,self.calibration,'e'*64)
        self.store=PersistenceStore(self.db.connection,self.context.database_uuid,expires_us=s.now+5_000_000,clock_us=lambda:s.now)
        self.grant=self.store.reserve_provenance(self.job(),uid(),block_count=256,current_context_sha256=self.context.sha256)
        self.collector=MapCheckpointCollector(self.scope,self.grant,now_us=s.now)
        parent,parent_context=self.collector.vehicle_parent(self.vehicle,admission=self.admission,now_us=s.now)
        def stamp(stream):return FrameStamp(self.raw.fields['acquisition_worker'],self.raw.fields['stream_instances'][stream],1,
            self.raw.fields['clock_epoch'],self.pose.captured_us,s.now)
        self.observation=PerceptionInput('LIVE',self.calibration.sha256,
            ImageFrame(stamp('rgb_video'),6,6,'NV12',bytes(54)),ImageFrame(stamp('depth_right'),6,6,'RAW16_LE',bytes(72)))
        sample=CaptureSample(RawFrame('rgb_video',1,400000,500000,s.now,6,6,'NV12',bytes(54),0),
            RawFrame('depth_right',1,400001,500001,s.now,6,6,'RAW16_LE',bytes(72),0),None,None,{})
        self.frame=FrameEvidence.from_capture(self.scope,self.observation,sample,pi_interval=(1_100_050,1_100_050),
            observation_sequence=1,parents=(parent,),contexts=(parent_context,),clock_lease=s.lease,now_us=s.now)
        self.profile=PipelineProfile(grid=GridSpec(1,1,.01,(-.005,-.005)),max_free_cells=1,max_free_tiles=64,max_free_ray_tests=256,
            permission_polygon=self.calibration.support_polygon,permission_source='EXPLICIT_SOFTWARE_FIXTURE')
        self.pipeline=PerceptionPipeline(self.calibration,self.profile,targets=(TargetClass(.5,.5,.5,qualified=True),))
        self.depth=DepthEvidence(self.observation.depth.stamp,self.calibration.sha256,'LIVE',(),
            tuple(replace(ray,source_key=self.observation.depth.stamp.key) for ray in depth_fixture.witness_rays()),9,9,0,0,0,0,True,'FLOOR')

    def job(self):return StorageJob(self.context,uid(),self.sensor.now,self.sensor.now+5_000_000)

    def process(self):
        import numpy as np
        graph=SimpleNamespace(status='UNKNOWN',strips=(),reasons=(),work={})
        road=SimpleNamespace(status='UNKNOWN',hypotheses=(),reasons=(),semantic_scored=0)
        with patch('xenova_vehicle.perception.depth.analyze_depth',return_value=self.depth), \
                patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=graph), \
                patch('xenova_vehicle.perception.pipeline.infer_road',return_value=road):
            result=self.pipeline.process(self.observation,pose=self.pose,now_us=self.sensor.now,
                completed_clock=lambda:self.sensor.now+1000,motion_qualified=True,needed_mask=np.ones((1,1),dtype=bool),
                cycle_bound_us=10_000,completion_bound_us=20_000,evidence_observer=self.collector.frame_observer(self.frame))
        self.sensor.now+=1000
        return result

    def test_actual_pipeline_to_sql_load_preserves_source_age_meta_and_restores_no_live_free(self):
        result=self.process();self.assertEqual(result.summary['free_cells_applied'],1)
        request=self.collector.capture(self.pipeline,self.job(),now_us=self.sensor.now,
            scene=dict(profile='existing-scene-fixture',unchanged='original'),
            subsystem_state={'lane_catalog':dict(profile='lane-catalog-state-1',catalog_revision=0,models=[])})
        receipt=self.store.commit(request.job,request.payload,current_context_sha256=self.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        self.assertFalse(self.collector._durable_tiles)
        self.assertTrue(self.collector.accept_commit(request,receipt,now_us=self.sensor.now))
        self.assertEqual(self.collector._durable_tiles[(0,0)],request.payload.tiles[0])
        cell=CELL.unpack_from(request.payload.tiles[0].payload)
        self.assertEqual(cell[7:10],(1_100_050,1_100_050,1))
        proof={v.provenance_id:v for v in request.payload.evidence.provenance}[cell[6]]
        self.assertEqual(proof.parents,(self.frame.parents[0].provenance_id,));self.assertEqual(proof.source_mask,18)
        manifest=json_bytes(request.payload.manifest,16384);self.assertEqual(manifest['scene']['unchanged'],'original')
        reference=self.store.latest_complete(expires_us=self.sensor.now+5_000_000)
        load_request=LoadRequest(self.job(),canonical(reference),canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        loaded=self.store.load(load_request,current_context_sha256=self.context.sha256)
        state,pages=decode_mapper_state(manifest['scene'],loaded.payload.evidence.contexts)
        self.assertTrue(pages);self.assertEqual(state['lane_catalog']['models'],[])
        complete=CheckpointPages(canonical(reference),self.context).accept(loaded,now_us=self.sensor.now)
        self.assertIsInstance(complete,SourceComplete)
        restored=PerceptionPipeline(self.calibration,self.profile,targets=self.pipeline.targets)
        self.assertIs(restore_pipeline(restored,complete,self.scope),complete)
        self.assertEqual(restored.grid.revision,self.pipeline.grid.revision)
        self.assertFalse(restored.grid.state.any() or restored.grid.time_known.any())
        self.assertEqual(restored.rgb_gate._heads,self.pipeline.rgb_gate._heads)
        self.assertEqual(restored.depth_gate._heads,self.pipeline.depth_gate._heads)
        self.assertIsNone(restored._alignment)
        old=json_bytes(complete.state,512*1024)
        self.assertEqual(old['clock_id'],self.observation.rgb.stamp.clock_id)
        self.assertEqual(complete.evidence.catalog_highwaters,(1,1))
        self.assertEqual(self.db.connection.execute(
            'SELECT allocator_highwater FROM storage_metadata WHERE singleton=1').fetchone()[0],str(self.grant.highwater_after))
        self.assertIs(complete.current_head(self.context.sha256,self.sensor.now),loaded.heads)
        self.assertIsNone(complete.current_head(self.context.sha256,load_request.job.expires_us))
        with self.assertRaises(MapCheckpointNotReady):SourceComplete()

    def test_original_source_written_mask_keeps_blocked_witness_and_prevents_fake_checkpoint(self):
        self.process();grid=self.pipeline.grid;events=[]
        before=dict(self.collector._cells)
        late=replace(self.observation.depth.stamp,captured_us=(self.pose.captured_us[0]-10,self.pose.captured_us[0]-5))
        def observer(delta):
            events.append(delta)
            self.collector.frame_observer(self.frame)(MapEvidenceUpdate(grid,delta,self.observation,self.depth,
                self.pose,self.pose,self.depth.patches))
        count=grid.add_hits(((-.001,-.001,.001,.001),),late,view_id=late.stream_id,origin_xy=(0.,0.),observer=observer)
        self.assertEqual(count,1);self.assertEqual(events[0].affected_mask,b'\1');self.assertEqual(events[0].source_written_mask,b'\0')
        self.assertEqual(self.collector._cells,before)
        with self.assertRaises(MapCheckpointNotReady):self.collector.capture(self.pipeline,self.job(),now_us=self.sensor.now)
        untimed=replace(late,captured_us=None)
        grid.add_hits(((-.001,-.001,.001,.001),),untimed,view_id=late.stream_id,origin_xy=(0.,0.),
            observer=lambda delta:self.collector.frame_observer(None)(MapEvidenceUpdate(grid,delta,self.observation,self.depth,
                self.pose,self.pose,self.depth.patches)))
        with self.assertRaises(MapCheckpointNotReady):self.collector.capture(self.pipeline,self.job(),now_us=self.sensor.now)

    def test_catalog_extension_is_explicit_and_cannot_change_map_proof_or_add_unrelated_sources(self):
        # 이 fixture는 extension의 구조 경계다. 실제 SLOT geometry/range/내구 승인 시험은 아니다.
        self.process();job=self.job();snapshot=self.collector.snapshot(self.pipeline,job,now_us=self.sensor.now)
        parent=snapshot.evidence.provenance[-1];identity=self.collector._allocate()
        context=next(c for c in snapshot.evidence.contexts if c.sha256==parent.context_sha256)
        aggregate=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),parent.source_mask,4,0,1,
            UUID(self.context.pi_host_boot).bytes,parent.pi_lower,parent.pi_upper,parent.observation_sequence,
            parent.provenance_id,0,0,0,bytes(128),0,bytes(16)))
        catalog=CatalogRecord('SLOT',1,1,0,identity,b'EXPLICIT_STORAGE_TEST_ONLY')
        evidence=replace(snapshot.evidence,provenance=snapshot.evidence.provenance+(aggregate,),catalog=(catalog,),catalog_highwaters=(1,2))
        def request(value):
            return CheckpointRequest(job,CheckpointPayload.build(job,snapshot.tiles,value,tile_references=snapshot.tile_references,
                scene=dict(mapper_state=snapshot.mapper_state_ref),durable_cutoff_pi_us=snapshot.durable_cutoff_pi_us))
        final=request(evidence)
        with self.assertRaisesRegex(MapCheckpointNotReady,'original map/source evidence'):self.collector.track_checkpoint(final)
        values=list(PROVENANCE.unpack(parent.payload));values[6]-=1
        changed=ProvenanceRecord(parent.provenance_id,PROVENANCE.pack(*values))
        mutated=replace(evidence,provenance=tuple(changed if p.provenance_id==changed.provenance_id else p for p in evidence.provenance))
        with self.assertRaisesRegex(MapCheckpointNotReady,'original proof'):self.collector.track_checkpoint(request(mutated),catalog_extension=True)
        unrelated=replace(aggregate,provenance_id=self.collector._allocate())
        extra=replace(evidence,provenance=evidence.provenance+(unrelated,))
        with self.assertRaisesRegex(MapCheckpointNotReady,'unrelated'):self.collector.track_checkpoint(request(extra),catalog_extension=True)
        self.assertIs(self.collector.track_checkpoint(final,catalog_extension=True),final)

    def test_missing_timing_or_different_original_capture_is_not_a_frame_proof(self):
        sample=CaptureSample(RawFrame('rgb_video',1,400000,500000,self.sensor.now,6,6,'NV12',bytes(54)),
            RawFrame('depth_right',1,400001,500001,self.sensor.now,6,6,'RAW16_LE',bytes(72)),None,None,{})
        for observation in (replace(self.observation,depth=replace(self.observation.depth,
                stamp=replace(self.observation.depth.stamp,captured_us=None))),
                replace(self.observation,depth=replace(self.observation.depth,payload=b'\1'+bytes(71)))):
            with self.assertRaises(MapCheckpointNotReady):
                FrameEvidence.from_capture(self.scope,observation,sample,pi_interval=(1_100_050,1_100_050),
                    observation_sequence=1,parents=self.frame.parents,contexts=self.frame.contexts,
                    clock_lease=self.sensor.lease,now_us=self.sensor.now)

    def _restored_nonidentity_fixture(self,*,raw_x,position_error,spacing_us,localization_limits=None):
        import test_alignment_flow as alignment_fixture
        from xenova_vehicle.mapping.registration import RegistrationProfile
        from xenova_vehicle.perception.geometry import PointBounds
        fixture=alignment_fixture.AlignmentFlowTests(methodName='runTest');fixture.setUp()
        profile=replace(self.profile,registration=RegistrationProfile(cell_steps=(.002,.002,.001),residual_m=.002,
            max_position_error_m=.03,max_yaw_error_rad=.01))
        pipeline=PerceptionPipeline(self.calibration,profile)
        hit=(PointBounds((-.001,-.001,.1),(.001,.001,.2),True),)
        def execute(core,observation,pose,graph,observer=None):
            depth=DepthEvidence(observation.depth.stamp,self.calibration.sha256,'LIVE',hit,(),1,0,0,0,0,0,True,'HIT_ONLY')
            with patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=graph), \
                    patch('xenova_vehicle.perception.depth.analyze_depth',return_value=depth):
                return core.process(observation,pose=pose,now_us=self.sensor.now,evidence_observer=observer,
                    **({} if localization_limits is None else localization_limits))
        result=execute(pipeline,self.observation,self.pose,fixture.graph(alignment_fixture.CORNER_GT,0.),
            self.collector.frame_observer(self.frame,pipeline=pipeline))
        self.collector.observe_result(self.frame,result,pipeline,raw_pose=self.pose)
        def next_frame(sequence,error):
            self.sensor.now+=spacing_us;pi_us=1_100_050+(sequence-1)*spacing_us
            message=self.sensor.vehicle(sequence,pi_us,raw_x);message.clock_error_us=0;message.position_error_m=error;message.heading_error_rad=.001
            message.source_us=420000+(sequence-1)*spacing_us
            message.velocity_mps=.5
            vehicle=self.admission.accept_vehicle(encode(message),self.sensor.lease,self.sensor.now)
            estimate=PoseEstimate(vehicle.pose_sample,vehicle.j_interval,vehicle.j_interval)
            parent,context=self.collector.vehicle_parent(vehicle,admission=self.admission,now_us=self.sensor.now)
            def stamp(name):return FrameStamp(self.raw.fields['acquisition_worker'],self.raw.fields['stream_instances'][name],sequence,
                self.raw.fields['clock_epoch'],estimate.captured_us,self.sensor.now)
            observation=PerceptionInput('LIVE',self.calibration.sha256,ImageFrame(stamp('rgb_video'),6,6,'NV12',bytes(54)),
                ImageFrame(stamp('depth_right'),6,6,'RAW16_LE',bytes(72)))
            delta=(sequence-1)*spacing_us
            sample=CaptureSample(RawFrame('rgb_video',sequence,400000+delta,500000+delta,self.sensor.now,6,6,'NV12',bytes(54),0),
                RawFrame('depth_right',sequence,400001+delta,500001+delta,self.sensor.now,6,6,'RAW16_LE',bytes(72),0),None,None,{})
            frame=FrameEvidence.from_capture(self.scope,observation,sample,pi_interval=(pi_us,pi_us),observation_sequence=sequence,
                parents=(parent,),contexts=(context,),clock_lease=self.sensor.lease,now_us=self.sensor.now,
                reference_vehicle=vehicle,admission=self.admission,
                registration_valid_for_us=100_000 if localization_limits is None else localization_limits['localization_valid_for_us'])
            return observation,estimate,frame
        observation,pose,frame=next_frame(2,position_error)
        result=execute(pipeline,observation,pose,fixture.graph(alignment_fixture.CORNER_GT,.1),
            self.collector.frame_observer(frame,pipeline=pipeline))
        self.assertEqual(result.registration.status,'BOUNDED')
        self.collector.observe_result(frame,result,pipeline,raw_pose=pose)
        self.assertLess(pipeline._alignment.pose[0],-.005)
        request=self.collector.capture(pipeline,self.job(),now_us=self.sensor.now)
        receipt=self.store.commit(request.job,request.payload,current_context_sha256=self.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        self.assertTrue(self.collector.accept_commit(request,receipt,now_us=self.sensor.now))
        ref=canonical(self.store.latest_complete(expires_us=self.sensor.now+5_000_000))
        load=LoadRequest(self.job(),ref,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        complete=CheckpointPages(ref,self.context).accept(self.store.load(load,current_context_sha256=self.context.sha256),now_us=self.sensor.now)
        restored=PerceptionPipeline(self.calibration,profile);restore_pipeline(restored,complete,self.scope)
        self.assertTrue(restored._restore_pending)
        return restored,pipeline,execute,next_frame

    def test_inherited_reference_error_exhausts_original_budget_and_keeps_restore_pending(self):
        # Nano 원 반례: 같은16cm prior라도 ref error가 약0.00054→0.011m로 커져 cap에 닿는다.
        restored,pipeline,execute,next_frame=self._restored_nonidentity_fixture(raw_x=.2,position_error=.16,spacing_us=20_000)
        grid,reference=restored.grid,restored.reference;revision=grid.revision
        observation,pose,_=next_frame(3,.00001)
        missing=TapeGraph((),status='UNKNOWN',complete=False,reasons=('NO_OBSERVED_TAPE',),calibration_qualified=True)
        withheld=execute(restored,observation,pose,missing)
        self.assertTrue(withheld.summary['quarantined']);self.assertEqual(withheld.summary['map_counts']['occupied'],1)
        self.assertTrue(restored._restore_pending);self.assertIs(restored.reference,reference)
        self.assertIs(restored.grid,grid);self.assertEqual(grid.revision,revision);self.assertFalse(grid.state.any())
        observation,pose,_=next_frame(4,.16)
        import test_alignment_flow as alignment_fixture
        fixture=alignment_fixture.AlignmentFlowTests(methodName='runTest');fixture.setUp()
        unknown=execute(restored,observation,pose,fixture.graph(alignment_fixture.CORNER_GT,.1))
        self.assertEqual(unknown.registration.status,'UNKNOWN_BUDGET')
        self.assertFalse(unknown.registration.complete)
        self.assertEqual(unknown.registration.tested_cells,pipeline.profile.registration.max_cells)
        self.assertTrue(restored._restore_pending and unknown.summary['quarantined'])
        self.assertIs(restored.reference,reference);self.assertEqual(grid.revision,revision);self.assertFalse(grid.state.any())
        self.assertIs(restored.grid,grid)
        self.assertFalse(unknown.summary['world_valid'] or unknown.summary['live_authority'])

    def test_independent_bounded_odometry_fixture_relocalizes_without_changing_caps(self):
        # 별도 DB/source/물리 가정이다. producer가 기존16cm 오차를 자동으로 줄이는 기능이 아니다.
        # 200ms에 true0.1m/odom0.11m 이동은 명시0.5m/s와1.2cm bound 안에 있다.
        restored,pipeline,execute,next_frame=self._restored_nonidentity_fixture(raw_x=.11,position_error=.012,spacing_us=200_000)
        import test_alignment_flow as alignment_fixture
        fixture=alignment_fixture.AlignmentFlowTests(methodName='runTest');fixture.setUp()
        grid,reference=restored.grid,restored.reference;revision=grid.revision
        observation,pose,_=next_frame(3,.012)
        missing=TapeGraph((),status='UNKNOWN',complete=False,reasons=('NO_OBSERVED_TAPE',),calibration_qualified=True)
        withheld=execute(restored,observation,pose,missing)
        self.assertTrue(withheld.summary['quarantined'] and restored._restore_pending)
        self.assertIs(restored.reference,reference);self.assertEqual(grid.revision,revision)
        observation,pose,_=next_frame(4,.012)
        admitted=execute(restored,observation,pose,fixture.graph(alignment_fixture.CORNER_GT,.1))
        self.assertEqual(admitted.registration.status,'BOUNDED');self.assertTrue(admitted.registration.complete)
        self.assertLessEqual(admitted.registration.tested_cells,4096)
        self.assertEqual(pipeline.profile.registration.max_cells,4096)
        self.assertEqual(pipeline.profile.registration.max_distance_tests,4194304)
        self.assertEqual(pipeline.profile.registration.cell_steps,(.002,.002,.001))
        self.assertEqual(pipeline.profile.registration.residual_m,.002)
        self.assertFalse(restored._restore_pending or admitted.summary['quarantined'])
        self.assertIs(restored.grid,grid)
        self.assertFalse(admitted.summary['world_valid'] or admitted.summary['live_authority'])

    def test_original_alignment_lifetime_clock_roundtrip_and_normal_frame_does_not_refresh_it(self):
        from xenova_vehicle.mapping.alignment import MapOdomAlignment
        limits=dict(localization_valid_for_us=800_000,localization_position_drift_mps=.001,localization_yaw_drift_rps=.001)
        restored,pipeline,execute,next_frame=self._restored_nonidentity_fixture(
            raw_x=.11,position_error=.012,spacing_us=200_000,localization_limits=limits)
        ref=canonical(self.store.latest_complete(expires_us=self.sensor.now+5_000_000))
        load=LoadRequest(self.job(),ref,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        complete=CheckpointPages(ref,self.context).accept(self.store.load(load,current_context_sha256=self.context.sha256),now_us=self.sensor.now)
        original=json_bytes(complete.state,512*1024);alignment=pipeline._alignment
        self.assertEqual(MapOdomAlignment.from_fields(original['alignment']),alignment)
        self.assertEqual(len(original['alignment']),9)
        self.assertEqual(original['alignment_clock']['pi_interval'],[1_300_050,1_300_050])
        self.assertEqual(original['alignment_clock']['accepted_j_us'],1_250_000)
        self.assertTrue(restored._restore_pending);self.assertIsNone(restored._alignment)
        self.assertFalse(alignment.valid_at(alignment.observed_j_interval[0]+alignment.valid_for_us))
        observation,pose,frame=next_frame(3,.012)
        missing=TapeGraph((),status='UNKNOWN',complete=False,reasons=('NO_OBSERVED_TAPE',),calibration_qualified=True)
        result=execute(pipeline,observation,pose,missing,self.collector.frame_observer(frame,pipeline=pipeline))
        self.assertIsNone(result.registration);self.assertIs(pipeline._alignment,alignment)
        # 누적 보수 오차가 임계를 넘으면 이 새 관측도 격리된다. 원 정합을 갱신할 근거는 없다.
        if not result.summary['quarantined']:self.collector.observe_result(frame,result,pipeline,raw_pose=pose)
        request=self.collector.capture(pipeline,self.job(),now_us=self.sensor.now)
        state,_=decode_mapper_state(json_bytes(request.payload.manifest,16384)['scene'],request.payload.evidence.contexts)
        self.assertEqual(state['alignment'],original['alignment'])
        self.assertEqual(state['alignment_clock'],original['alignment_clock'])
        self.assertEqual(state['alignment_provenance'],original['alignment_provenance'])
        record=next(v for v in request.payload.evidence.provenance if v.provenance_id==state['alignment_provenance'])
        self.assertEqual((record.pi_lower,record.pi_upper),(1_300_050,1_300_050))

    def test_original_registration_rejects_rewritten_metadata_after_real_checkpoint_and_load(self):
        from xenova_vehicle.runtime.map_checkpoint import MapRestoreNotReady
        limits=dict(localization_valid_for_us=800_000,localization_position_drift_mps=.001,localization_yaw_drift_rps=.001)
        _,pipeline,_,_=self._restored_nonidentity_fixture(raw_x=.11,position_error=.012,spacing_us=200_000,localization_limits=limits)
        ref=canonical(self.store.latest_complete(expires_us=self.sensor.now+5_000_000))
        load=LoadRequest(self.job(),ref,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        loaded=self.store.load(load,current_context_sha256=self.context.sha256)
        manifest=json_bytes(loaded.payload.manifest,16384)
        state,old_pages=decode_mapper_state(manifest['scene'],loaded.payload.evidence.contexts)
        state['alignment']['registration_revision']+=1
        metadata,pages=encode_mapper_state(state);scene=dict(manifest['scene'],mapper_state=metadata)
        original_contexts=tuple(v for v in loaded.payload.evidence.contexts if v not in old_pages)
        evidence=replace(loaded.payload.evidence,contexts=original_contexts+pages)
        job=self.job();payload=CheckpointPayload.build(job,(),evidence,tile_references=loaded.payload.tile_references,
            scene=scene,durable_cutoff_pi_us=manifest['durable_cutoff_pi_us'])
        self.assertEqual(self.store.commit(job,payload,current_context_sha256=self.context.sha256).status,'COMMITTED')
        ref=canonical(self.store.latest_complete(expires_us=self.sensor.now+5_000_000))
        load=LoadRequest(self.job(),ref,canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))
        complete=CheckpointPages(ref,self.context).accept(self.store.load(load,current_context_sha256=self.context.sha256),now_us=self.sensor.now)
        candidate=PerceptionPipeline(self.calibration,pipeline.profile)
        with self.assertRaisesRegex(MapRestoreNotReady,'revision/validity'):
            restore_pipeline(candidate,complete,self.scope)
        self.assertIsNone(candidate.grid)

    def test_full_128_tile_actual_load_requires_all_four_original_pages_and_closure(self):
        # 실제 SQLite의128 tile을 네 개32-tile copy로 누적한다. 처리율/5초 수락 시험은 아니다.
        context=self.scope.context_record('RAW');identity=self.collector._allocate()
        raw=struct.pack('<16sQQ',UUID(self.raw.fields['stream_instances']['depth_right']).bytes,1,400001)
        proof=ProvenanceRecord(identity,PROVENANCE.pack(bytes.fromhex(context.sha256),2,0,1,0,
            UUID(self.context.pi_host_boot).bytes,1_100_050,1_100_050,1,0,0,0,0,raw.ljust(128,b'\0'),0,bytes(16)))
        evidence=EvidenceTable((proof,),(context,));unknown=CELL.pack(255,0,0,0.,0.,0.,0,0,0,0,0,0)
        cell=CELL.pack(100,1,2,.1,.2,.01,identity,1_100_050,1_100_050,1,1,0)
        tiles=tuple(TileVersion(self.context.map_epoch,index,0,1,cell+unknown*4095) for index in range(128))
        for end in (32,64,96,128):
            job=self.job();payload=CheckpointPayload.build(job,tiles[end-32:end],evidence,
                tile_references=tuple(tile.reference for tile in tiles[:end]),durable_cutoff_pi_us=1_100_050)
            self.assertEqual(self.store.commit(job,payload,current_context_sha256=self.context.sha256).status,'COMMITTED')
        from xenova_vehicle.runtime.storage_messages import make_load_request
        ref=canonical(self.store.latest_complete(expires_us=self.sensor.now+5_000_000));pages=None
        binding=dict(service_instance=self.raw.fields['service_instance'],storage_worker=uid(),storage_profile_sha256='d'*64,
            database_uuid=self.context.database_uuid,db_schema_version=1)
        contexts=[]
        for cursor in (0,32,64,96):
            request=make_load_request(binding,mapper_worker=self.context.mapper_worker,origin_mapper_instance=self.context.origin_mapper_instance,
                job_id=uid(),issued_us=self.sensor.now,expires_us=self.sensor.now+5_000_000,checkpoint_ref=json_bytes(ref,2048),
                selection=dict(kind='CHECKPOINT_PAGE',cursor=cursor),purpose='BOOTSTRAP_RESTORE')
            if pages is None:pages=CheckpointPages(ref,request.job.context)
            contexts.append(request.job.context.sha256)
            loaded=self.store.load(request,current_context_sha256=request.job.context.sha256)
            if cursor==0:
                with self.assertRaises(MapCheckpointNotReady):CheckpointPages(ref,request.job.context).accept(
                    replace(loaded,request=replace(request,selection=canonical(dict(kind='CHECKPOINT_PAGE',cursor=32)))),now_us=self.sensor.now)
            complete=pages.accept(loaded,now_us=self.sensor.now)
            if cursor<96:self.assertIsNone(complete)
        self.assertEqual(len(complete.tiles),128);self.assertEqual(len(complete.page_receipts),4)
        self.assertEqual(len(set(contexts)),4)
        self.assertEqual(complete.evidence.provenance,(proof,));self.assertEqual(complete.checkpoint_ref,ref)
        self.assertIsNone(complete.state)
        with self.assertRaises(MapCheckpointNotReady):restore_pipeline(self.pipeline,complete,self.scope)


class MapperStateCodecTests(unittest.TestCase):
    def test_only_explicit_exact_page_refs_are_loaded_and_corruption_never_becomes_legacy(self):
        state=dict(profile='mapper-state-fixture',opaque_bytes=os.urandom(9000).hex())
        ref,pages=encode_mapper_state(state);self.assertGreater(len(pages),1)
        scene=dict(profile='original-survey',mapper_state=ref)
        self.assertEqual(decode_mapper_state(scene,pages)[0],state)
        with self.assertRaises(PersistenceError):decode_mapper_state(scene,pages[:-1])
        body=json_bytes(pages[0].payload,4096);body['body']['index']=1
        corrupt=ContextRecord(canonical(body))
        with self.assertRaises(PersistenceError):decode_mapper_state(scene,(corrupt,*pages[1:]))
        self.assertEqual(decode_mapper_state(dict(profile='legacy'),pages),(None,()))
        self.assertEqual(sha256(canonical(dict(profile='legacy'))).hexdigest(),sha256(b'{"profile":"legacy"}').hexdigest())
