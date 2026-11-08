"""실제 bracket→후보 collector→원 mailbox→SQLite CP/LOAD의 작은 연결 회귀.

카메라/peer 인증은 기존 software fixture다. SQLite는 실제 임시 파일을 사용하되
주입한 J 시각으로 수명만 검산한다. native 반납·실차 보정·처리율/권한 수락 시험은 아니다.
"""

from dataclasses import replace
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_mapper_sensor_state as sensor_fixtures
import test_mapper_profile as profile_fixtures
from envelope_fixtures import fields
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.perception.depth import DepthEvidence
from xenova_vehicle.perception.geometry import PointBounds
from xenova_vehicle.perception.inputs import FrameStamp,ImageFrame,PerceptionInput
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.perception.tape import TapeGraph
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.map_checkpoint import FrameEvidence,MapCheckpointNotReady
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_mapping import MappingContextPort,MapperMapping
from xenova_vehicle.runtime.mapper_profile import MapperProfile
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox
from xenova_vehicle.runtime.resident_samples import CaptureSample
from xenova_vehicle.runtime.resident_storage import _Database,StorageProfile
from xenova_vehicle.runtime.storage_messages import DbMessage
from xenova_vehicle.storage.contracts import StorageJob,canonical
from xenova_vehicle.storage.records import PROVENANCE
from xenova_vehicle.storage.repository import PersistenceStore


def uid():return str(uuid4())


@unittest.skipUnless(os.name=='posix','실제 SQLite/writer lock 검사는 대상 POSIX에서 한다')
class MapperMappingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=sensor_fixtures.MapperSensorStateTests(methodName='runTest');self.fixture.setUp();f=self.fixture
        self.now=f.now
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.db=_Database(StorageProfile(str(Path(temporary.name)/'mapping.sqlite3'),'CREATE_IF_ABSENT'),time.monotonic_ns()//1000+5_000_000)
        self.addCleanup(self.db.close)
        self.context=replace(f.context,database_uuid=self.db.metadata['database_uuid'])
        value=f.snapshot();value['storage_context']=self.context.fields;value['context_sha256']=self.context.sha256
        self.source_bytes=canonical(value)
        self.inputs=f.state.read(self.source_bytes,f.stamp,now_us=self.now)
        settings=profile_fixtures.profile_fields(f.calibration)
        settings.update(vehicle_configuration=f.fixture.source['configuration'],motion_qualified=True,
            localization=dict(registration_valid_for_us=100_000,position_drift_mps=.001,yaw_drift_rps=.001))
        settings['pipeline']['grid'].update(width=4,height=4,resolution_m=.1,origin_xy=(-.2,-.2))
        settings['pipeline'].update(permission_polygon=f.calibration.support_polygon,permission_source='EXPLICIT_SOFTWARE_FIXTURE')
        settings['parking_aisles']=[]
        for target in settings['targets']:target['qualified']=True
        settings['sensors'].update(vehicle_max_age_us=200_000,tof_max_age_us=200_000,capacity=16)
        settings['sensors']['tof_sources']=[dict(external_leaf_name=name,leaf=leaf.to_dict()) for name,leaf in f.leaves.values()]
        self.profile=MapperProfile(settings)
        self.port=MappingContextPort();self.port.publish(self.context)
        self.mailbox=MapperStorageMailbox(self.context.sha256)
        binding=dict(service_instance=f.raw.fields['service_instance'],storage_worker=uid(),storage_profile_sha256=self.db.profile.sha256,
            database_uuid=self.context.database_uuid,db_schema_version=1)
        bootstrap=dict(status='EMPTY',allocator_ready=True,schema_version=1,database_uuid=self.context.database_uuid,
            allocator_highwater=self.db.metadata['allocator_highwater'],selected_checkpoint=None)
        self.material=binding,bootstrap
        self.mapping=self.owner()
        self.store=PersistenceStore(self.db.connection,self.context.database_uuid,expires_us=self.now+5_000_000,clock_us=lambda:self.now)

    def owner(self,profile=None):
        return MapperMapping(self.port,self.mailbox,self.fixture.raw,self.fixture.calibration,
            self.profile if profile is None else profile,initial_storage_material=self.material,provenance_block_count=256)

    def grant(self,*,late=False):
        self.assertEqual(self.mapping.maintenance(now_us=self.now),'PROVENANCE_OFFERED')
        original=self.mapping.pending.request
        dispatch=self.mailbox.main_take(current_context_sha256=self.context.sha256,now_us=self.now)
        actual=self.store.reserve_provenance(original.job,original.reservation_nonce,block_count=original.block_count,
            current_context_sha256=self.context.sha256)
        body=actual.fields
        outer=fields('DB_JOB',payload=dict(op='RANGE_RESULT',body=body),domain='STORAGE')
        outer.update(request_id=original.job.job_id,issued_j_us=str(self.now),expires_j_us=str(original.job.expires_us))
        packet=encode_structure(outer);message=DbMessage(packet,'RANGE_RESULT',canonical(body),self.now,original.job.expires_us)
        self.mailbox.main_complete(dispatch,message,completed_us=self.now);self.mailbox.main_release(dispatch)
        if late:self.now=original.job.expires_us
        self.mapping.maintenance(now_us=self.now)
        return original

    def item(self):
        f=self.fixture;at=self.inputs.pose.captured_us
        rgb=RawFrame('rgb_video',12,400000,500000,self.now,640,360,'NV12',bytes(640*360*3//2),0)
        depth=RawFrame('depth_right',12,400001,500001,self.now,640,480,'RAW16_LE',bytes(640*480*2),0)
        clocks={name:dict(bridge_sample=dict(j_before_us=str(self.now),sdk_us=str(self.now),j_after_us=str(self.now)),
            sdk_host_timestamp_j_bracket_us=[str(at[0]),str(at[1])]) for name in ('rgb_video','depth_right')}
        sample=CaptureSample(rgb,depth,None,None,clocks)
        item=MapperInput(uid(),f.raw.sha256,sample,self.now+150_000,context_bytes=self.source_bytes)
        def stamp(name):return FrameStamp(f.raw.fields['acquisition_worker'],f.raw.fields['stream_instances'][name],12,
            f.raw.fields['clock_epoch'],at,self.now)
        observation=PerceptionInput('LIVE',f.calibration.sha256,
            ImageFrame(stamp('rgb_video'),640,360,'NV12',rgb.payload),ImageFrame(stamp('depth_right'),640,480,'RAW16_LE',depth.payload))
        return item,observation

    def frame(self,pipeline):
        item,observation=self.item()
        draft=self.mapping.prepare_frame(item,pipeline,observation,self.inputs,self.fixture.state,self.now)
        self.assertTrue(draft.ready,draft.reason)
        box=PointBounds((.001,.001,.1),(.002,.002,.2),True)
        depth=DepthEvidence(observation.depth.stamp,self.fixture.calibration.sha256,'LIVE',(box,),(),1,0,0,0,0,0,True,'HIT_ONLY')
        graph=TapeGraph((),status='UNKNOWN',complete=False,calibration_qualified=True)
        with patch('xenova_vehicle.perception.depth.analyze_depth',return_value=depth), \
                patch('xenova_vehicle.perception.pipeline.extract_tape',return_value=graph):
            result=pipeline.process(observation,pose=self.inputs.pose,now_us=self.now,evidence_observer=draft.observer,
                localization_valid_for_us=100_000,localization_position_drift_mps=.001,localization_yaw_drift_rps=.001)
        return self.mapping.finish_frame(draft,result,now_us=self.now)

    def test_two_original_bracket_parents_candidate_cancel_and_actual_sql_checkpoint_head_load(self):
        self.grant();accepted=self.mapping.collector
        candidate=PerceptionPipeline(self.fixture.calibration,self.profile.pipeline)
        draft=self.frame(candidate);self.assertTrue(draft.ready,draft.reason)
        self.assertEqual(len(draft.frame.parents),2)
        first=self.fixture.state.original_pose_bracket(self.inputs.pose).originals[0].evidence
        self.assertFalse(self.fixture.state.sensors.current_vehicle(first,self.now))
        sources=[PROVENANCE.unpack(row.payload)[6:9] for row in draft.frame.parents]
        self.assertEqual(tuple(sources),tuple((*entry.evidence.pi_interval,entry.evidence.anchor.sequence)
            for entry in self.fixture.state.original_pose_bracket(self.inputs.pose).originals))
        with self.assertRaisesRegex(MapCheckpointNotReady,'endpoint'):
            FrameEvidence.from_capture(draft.frame.scope,draft.frame.observation,draft.original_job.sample,
                pi_interval=draft.frame.pi_interval,observation_sequence=draft.frame.observation_sequence,
                parents=draft.frame.parents[1:],contexts=draft.frame.contexts,clock_lease=draft.frame.clock_lease,now_us=self.now,
                pose_bracket=self.fixture.state.original_pose_bracket(self.inputs.pose),sensor_state=self.fixture.state)
        burned=draft.collector._next
        self.assertIs(self.mapping.collector,accepted);self.assertFalse(accepted._cells)
        self.assertIsNone(self.mailbox.main_peek())
        self.mapping.cancel(draft);self.assertIs(self.mapping.collector,accepted)
        draft=self.frame(PerceptionPipeline(self.fixture.calibration,self.profile.pipeline))
        self.assertGreaterEqual(min(draft.collector._records),burned)
        self.mapping.accept(draft);self.assertTrue(self.mapping.collector._cells)
        self.assertIsNone(self.mailbox.main_peek());self.assertFalse(self.mapping._head_ready)
        job=StorageJob(self.context,uid(),self.now,self.now+5_000_000)
        request=self.mapping.queue_checkpoint(job,now_us=self.now)
        self.assertIsNone(self.mailbox.main_peek())
        self.mapping.maintenance(now_us=self.now)
        dispatch=self.mailbox.main_take(current_context_sha256=self.context.sha256,now_us=self.now)
        self.assertIs(dispatch.request,request)
        receipt=self.store.commit(request.job,request.payload,current_context_sha256=self.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        self.mailbox.main_complete(dispatch,receipt,completed_us=self.now);self.mailbox.main_release(dispatch)
        self.assertEqual(self.mapping.maintenance(now_us=self.now),'HEAD_LOAD_OFFERED')
        self.assertFalse(self.mapping._head_ready)
        dispatch=self.mailbox.main_take(current_context_sha256=self.context.sha256,now_us=self.now)
        loaded=self.store.load(dispatch.request,current_context_sha256=self.context.sha256)
        self.mailbox.main_complete(dispatch,loaded,completed_us=self.now);self.mailbox.main_release(dispatch)
        self.mapping.maintenance(now_us=self.now);self.assertTrue(self.mapping._head_ready)
        self.assertEqual(self.mapping.catalogs._head[0],loaded.heads)
        second=self.mapping.queue_checkpoint(StorageJob(self.context,uid(),self.now,self.now+5_000_000),now_us=self.now)
        self.assertIsNot(second,request);self.assertIsNone(self.mailbox.main_peek())
        self.assertEqual(self.mapping.catalogs.pending,None)
        collector=self.mapping.collector;cursor=collector._next;grant=collector.grant
        binding=dict(self.fixture.binding,source_binding_revision='2')
        changed=replace(self.context,source_binding=canonical(binding));self.port.publish(changed)
        self.assertEqual(self.mapping.maintenance(now_us=self.now+1),'WAIT_ORIGINAL_CONTEXT_TERMINAL')
        terminal=self.mapping.take_terminal()
        self.assertIs(terminal.request,second);self.assertFalse(terminal.current)
        self.assertEqual(terminal.result.reason,'SOURCE_REPLACED')
        self.assertEqual(terminal.observed_us,self.now+1)
        self.assertIsNone(self.mapping.catalogs._queued);self.assertIsNone(collector._pending)
        self.assertIs(self.mapping.collector,collector);self.assertIs(collector.grant,grant)
        self.assertEqual(collector._next,cursor)
        self.assertEqual(self.mapping.maintenance(now_us=self.now+2),'HEAD_LOAD_OFFERED')
        # 새 문맥의 실제 head LOAD만 게시한다. 이전 CP를 다시 게시하지 않는다.
        self.assertEqual(self.mapping.pending.role,'HEAD_LOAD')
        self.assertIs(self.mapping.pending.request.job.context,changed)

    def test_late_original_grant_is_history_and_is_never_automatically_reissued(self):
        original=self.grant(late=True)
        self.assertFalse(self.mapping.ready);self.assertIsNone(self.mapping.collector)
        self.assertIs(self.mapping.take_terminal().request,original)
        self.assertEqual(self.mapping.maintenance(now_us=self.now+1),'PROVENANCE_ORIGINAL_TERMINAL')
        self.assertIsNone(self.mailbox.main_peek())
        self.assertIs(self.mapping._range_request,original)

    def test_same_geometry_publication_keeps_original_grant_cursor_and_has_no_second_request(self):
        original=self.grant();collector=self.mapping.collector;grant=collector.grant
        self.port.publish(self.context)
        self.mapping.maintenance(now_us=self.now+1)
        self.assertIs(self.mapping.collector,collector);self.assertIs(self.mapping._range_request,original)
        binding=dict(self.fixture.binding,source_binding_revision='2')
        changed=replace(self.context,source_binding=canonical(binding));self.port.publish(changed)
        self.mapping.maintenance(now_us=self.now+2)
        self.assertIs(self.mapping.collector,collector);self.assertIs(collector.grant,grant)
        self.assertIs(grant.request,original);self.assertIs(original.job.context,self.context)
        self.assertEqual(collector._next,grant.start_inclusive)
        self.assertEqual(collector.scope.context,changed)
        self.assertIsNone(self.mailbox.main_peek())

    def test_missing_physical_timing_has_zero_storage_offer(self):
        for missing in ('capture_timing','target_qualification'):
            fields=self.profile.to_dict()
            if missing=='capture_timing':fields['capture_timing']=None
            else:fields['targets'][0]['qualified']=False
            with self.subTest(missing=missing):
                owner=self.owner(MapperProfile(fields))
                self.assertEqual(owner.maintenance(now_us=self.now),'PROFILE_NOT_READY')
                self.assertIsNone(owner.pending);self.assertIsNone(owner.collector)
                self.assertIsNone(self.mailbox.main_peek())
