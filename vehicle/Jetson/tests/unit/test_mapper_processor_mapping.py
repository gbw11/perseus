"""실제 Processor의 원 source/collector 후보 채택 경계를 검사한다.

원 Vehicle/clock/RGB bytes와 core는 기존 software fixture이며 range wire는 명시한
transport 사건이다. SQL/native 반환·실물 시계/보정·주행 권한의 수락 시험은 아니다.
"""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_mapper_runtime_profile as runtime_fixture
from envelope_fixtures import fields
from xenova_drive_world import encode
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.mapper_mapping import MappingContextPort
from xenova_vehicle.runtime.mapper_processor import ResidentMapperProcessor
from xenova_vehicle.runtime.mapper_profile import MapperProfile
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox
from xenova_vehicle.runtime.storage_messages import DbMessage
from xenova_vehicle.storage.contracts import canonical,json_bytes


def uid():return str(uuid4())
def checkpoint(*_):return None


class MapperProcessorMappingTests(unittest.TestCase):
    def setUp(self):
        self.fixture=runtime_fixture.MapperRuntimeProfileTests(methodName='runTest');self.fixture.setUp();f=self.fixture
        value=deepcopy(f.fields)
        value['localization']=dict(registration_valid_for_us=100_000,position_drift_mps=.001,yaw_drift_rps=.001)
        value['pipeline'].update(permission_polygon=f.calibration.support_polygon,permission_source='EXPLICIT_SOFTWARE_FIXTURE')
        value['parking_aisles']=[]
        for target in value['targets']:target['qualified']=True
        self.config=MapperProfile(value);self.port=MappingContextPort()
        self.context=f.fixture.context
        self.mailbox=MapperStorageMailbox(self.context.sha256)
        binding=dict(service_instance=f.raw.fields['service_instance'],storage_worker=uid(),storage_profile_sha256='d'*64,
            database_uuid=self.context.database_uuid,db_schema_version=1)
        bootstrap=dict(status='EMPTY',allocator_ready=True,schema_version=1,database_uuid=self.context.database_uuid,
            allocator_highwater='1',selected_checkpoint=None)
        self.material=canonical(dict(binding=binding,bootstrap=bootstrap))
        self.processor=ResidentMapperProcessor(f.calibration_bytes,f.raw,clock=lambda:f.now,
            mapper_profile_bytes=self.config.canonical_bytes,source_policy=f.source_policy,clock_policy=f.fixture.policy,
            mapper_worker=f.fixture.mapper_worker,mapper_instance=f.fixture.mapper_instance,
            mapping_context_port=self.port,mapping_mailbox=self.mailbox)

    def grant(self,expected_status='PROVENANCE_OFFERED'):
        self.port.publish(self.context,material=self.material)
        self.assertEqual(self.processor.maintenance(),expected_status)
        request=self.processor._mapping.pending.request
        dispatch=self.mailbox.main_take(current_context_sha256=self.context.sha256,now_us=self.fixture.now)
        body=dict(database_uuid=self.context.database_uuid,mapper_worker=self.context.mapper_worker,
            reservation_nonce=request.reservation_nonce,start_inclusive='1',end_exclusive=str(1+request.block_count),
            highwater_after=str(1+request.block_count),result='GRANTED')
        outer=fields('DB_JOB',payload=dict(op='RANGE_RESULT',body=body),domain='STORAGE')
        outer.update(request_id=request.job.job_id,issued_j_us=str(self.fixture.now),expires_j_us=str(request.job.expires_us))
        message=DbMessage(encode_structure(outer),'RANGE_RESULT',canonical(body),self.fixture.now,request.job.expires_us)
        self.mailbox.main_complete(dispatch,message,completed_us=self.fixture.now);self.mailbox.main_release(dispatch)
        self.processor.maintenance()
        self.assertTrue(self.processor._mapping.ready)

    def geometry(self,number=1,*,snapshot=None):
        item=self.fixture.item(number,snapshot=snapshot)
        result=json.loads(self.processor.geometry(item,None,checkpoint))['latest']
        return item,result

    def test_context_material_wait_diagnostic_then_actual_observer_and_cheap_final_adoption(self):
        processor=self.processor;original=processor._accepted
        self.assertEqual(processor.maintenance(),'WAIT_MATERIAL');self.assertIsNone(processor._mapping)
        self.port.publish(self.context,material=self.material)
        self.port.publish(self.context)
        self.assertIs(self.port.read(),self.context);self.assertIs(self.port.read_material(),self.material)
        self.assertEqual(processor.maintenance(),'PROVENANCE_OFFERED')
        pending=processor._mapping.pending.request
        item,summary=self.geometry()
        self.assertFalse(summary['mapping_ready']);self.assertEqual(summary['mapping_reason'],'PROVENANCE_NOT_READY')
        self.assertIsNone(processor._staged[1].pipeline.grid)
        self.assertIsNotNone(processor._staged[3].pipeline.grid)
        processor.accepted(item)
        self.assertIs(processor._accepted,original)
        self.assertEqual(processor._accepted_sensors.sensors.latest_vehicle.anchor.sequence,2)
        self.assertIs(self.mailbox.main_peek().request,pending)
        # 동일 원 예약을 완료한다. 새 원 UUID/nonce/TTL을 만들어 grant를 대신하지 않는다.
        dispatch=self.mailbox.main_take(current_context_sha256=self.context.sha256,now_us=self.fixture.now)
        body=dict(database_uuid=self.context.database_uuid,mapper_worker=self.context.mapper_worker,
            reservation_nonce=pending.reservation_nonce,start_inclusive='1',end_exclusive=str(1+pending.block_count),
            highwater_after=str(1+pending.block_count),result='GRANTED')
        outer=fields('DB_JOB',payload=dict(op='RANGE_RESULT',body=body),domain='STORAGE')
        outer.update(request_id=pending.job.job_id,issued_j_us=str(self.fixture.now),expires_j_us=str(pending.job.expires_us))
        message=DbMessage(encode_structure(outer),'RANGE_RESULT',canonical(body),self.fixture.now,pending.job.expires_us)
        self.mailbox.main_complete(dispatch,message,completed_us=self.fixture.now);self.mailbox.main_release(dispatch)
        processor.maintenance()
        captured=[];actual=PerceptionPipeline.process
        def observe(core,observation,**kwargs):
            captured.append((core,kwargs));return actual(core,observation,**kwargs)
        with patch.object(PerceptionPipeline,'process',new=observe):item,summary=self.geometry(2)
        draft=processor._mapping_draft
        self.assertTrue(summary['mapping_ready']);self.assertTrue(callable(captured[0][1]['evidence_observer']))
        self.assertEqual(len(draft.frame.parents),2)
        self.assertIs(processor._accepted,original);self.assertIsNone(self.mailbox.main_peek())
        processor.accepted(item)
        self.assertIs(processor._accepted.pipeline,captured[0][0])
        self.assertIs(processor._mapping.collector,draft.collector)
        self.assertIsNone(processor._mapping_draft);self.assertIsNone(processor._mapping._staged)
        self.assertIsNotNone(processor._retired)
        self.assertFalse(summary['world_valid'] or summary['live_authority'])
        processor.maintenance();self.assertIsNone(processor._retired)
        with self.assertRaises(ValueError):self.port.publish(self.context,material=bytes(2049))

    def test_process_exception_cleans_original_mapping_stage_and_allows_next_maintenance(self):
        self.grant();processor=self.processor;accepted=processor._accepted
        def failure(*args,**kwargs):raise RuntimeError('EXPLICIT_GEOMETRY_FAILURE')
        with patch.object(PerceptionPipeline,'process',side_effect=failure):
            with self.assertRaisesRegex(RuntimeError,'EXPLICIT_GEOMETRY_FAILURE'):self.geometry()
        self.assertIsNone(processor._staged);self.assertIsNone(processor._mapping_draft)
        self.assertIsNone(processor._mapping._staged);self.assertIsNone(processor._mapping.catalogs._staged)
        self.assertIs(processor._accepted,accepted);self.assertIsNone(accepted.pipeline.grid)
        self.assertIsNone(self.mailbox.main_peek())
        processor.cancelled(self.fixture.item(),'AFTER_FAILURE')
        self.assertEqual(processor.maintenance(),'IDLE')

    def test_final_cancel_keeps_metric_gates_and_original_sensor_admissions_without_reoffering(self):
        self.grant();processor=self.processor
        item,summary=self.geometry();self.assertTrue(summary['mapping_ready']);processor.accepted(item)
        accepted,sensors=processor._accepted,processor._accepted_sensors
        source=self.fixture.fixture.snapshot()
        third=encode(self.fixture.fixture.fixture.vehicle(3,1_120_000,.003))
        source['vehicles'].append(self.fixture.fixture.row(third,received=1_025_000))
        item,summary=self.geometry(2,snapshot=source)
        self.assertTrue(summary['mapping_ready']);self.assertEqual(len(processor._mapping_draft.frame.parents),3)
        self.assertEqual(processor._staged[2].sensors.latest_vehicle.anchor.sequence,3)
        self.assertIs(processor._accepted,accepted)
        old_heads=dict(accepted.pipeline.rgb_gate._heads)
        processor.cancelled(item,'ORIGINAL_INPUT_EXPIRED')
        self.assertIs(processor._accepted,accepted);self.assertIs(processor._accepted_sensors,sensors)
        self.assertEqual(accepted.pipeline.rgb_gate._heads,old_heads)
        self.assertNotIn(sha256(third).hexdigest(),sensors._seen_vehicle)
        self.assertIsNone(processor._mapping._staged);self.assertIsNone(processor._staged)
        self.assertIsNone(self.mailbox.main_peek())
        processor.maintenance();self.assertIsNone(self.mailbox.main_peek())

    def test_scene_only_context_change_before_adoption_cancels_without_advancing_metric_map(self):
        from xenova_vehicle.runtime.mapper_compute import MapperCancelled
        self.grant();processor=self.processor;original=processor._accepted
        item,summary=self.geometry();self.assertTrue(summary['mapping_ready'])
        draft=processor._mapping_draft;burned=processor._mapping._cursor_floor
        self.port.publish(replace(self.context))
        self.assertIs(self.port.read(),self.context)
        binding=json_bytes(self.context.source_binding,2048);binding['source_binding_revision']='2'
        updated=replace(self.context,source_binding=canonical(binding))
        self.port.publish(updated)
        with self.port.adoption_lock,self.assertRaisesRegex(MapperCancelled,'PUBLICATION_CHANGED'):
            processor.accepted(item)
        processor.cancelled(item,'PUBLICATION_CHANGED')
        self.assertIs(processor._accepted,original);self.assertIsNone(original.pipeline.grid)
        self.assertIsNone(processor._mapping._staged);self.assertIsNone(processor._staged)
        self.assertGreaterEqual(processor._mapping._cursor_floor,burned)
        self.assertIsNot(processor._mapping.collector,draft.collector)

    def test_explicit_processor_schedule_queues_actual_accepted_map_outside_final_adoption(self):
        fixture=self.fixture
        self.processor=ResidentMapperProcessor(fixture.calibration_bytes,fixture.raw,clock=lambda:fixture.now,
            mapper_profile_bytes=self.config.canonical_bytes,source_policy=fixture.source_policy,clock_policy=fixture.fixture.policy,
            mapper_worker=fixture.fixture.mapper_worker,mapper_instance=fixture.fixture.mapper_instance,
            mapping_context_port=self.port,mapping_mailbox=self.mailbox,checkpoint_interval_us=1000)
        self.grant(expected_status='WAIT_ORIGINAL_STORAGE')
        item,summary=self.geometry();self.assertTrue(summary['mapping_ready'])
        self.processor.accepted(item)
        self.assertIsNone(self.mailbox.main_peek());self.assertIsNone(self.processor._mapping.pending)
        self.assertEqual(self.processor.maintenance(),'DIRTY_BATCH_WAIT')
        fixture.now+=1000
        self.assertEqual(self.processor.maintenance(),'CHECKPOINT_QUEUED')
        original=self.processor._mapping.pending.request
        self.assertIsNone(self.mailbox.main_peek())
        self.assertEqual(self.processor.maintenance(),'WAIT_ORIGINAL_STORAGE')
        self.assertIs(self.mailbox.main_peek().request,original)
        self.assertEqual(original.job.issued_us,fixture.now)
