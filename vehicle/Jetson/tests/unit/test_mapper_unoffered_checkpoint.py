"""미게시 CP 철회와 이미 offer된 원 작업의 경계를 검사한다.

기존 합성 인지 fixture/실제 mailbox를 재사용한다. Mapping fixture의 임시 SQLite는
allocator 보존 확인용이며, 합성 UNKNOWN/반납 사건을 실제 D/native 회수로 부르지 않는다.
Scene descriptor 철회는 CP 생성 전 호출한다. Mapping 상태의 미게시 확인을 대신하지 않는다.
"""

from dataclasses import replace
import os
import unittest
from uuid import UUID,uuid4

import test_mapper_catalogs as catalog_fixture
import test_mapper_mapping as mapping_fixture
import test_mapper_scene as scene_fixture
from xenova_drive_world import schema,encode,decode
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.runtime.mapper_catalogs import CatalogNotReady
from xenova_vehicle.runtime.mapper_mapping import MappingNotReady
from xenova_vehicle.runtime.mapper_storage import StorageAbort
from xenova_vehicle.runtime.scene import SceneError
from xenova_vehicle.storage.contracts import StorageJob,CommitReceipt
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import EvidenceTable,CheckpointPayload


def uid():return str(uuid4())


@unittest.skipUnless(os.name=='posix','실제 D writer fixture는 대상 POSIX에서 확인한다')
class UnofferedCheckpointTests(unittest.TestCase):
    def fixture(self,kind):
        fixture=kind(methodName='runTest');self.addCleanup(fixture.doCleanups);fixture.setUp();return fixture

    def test_mapping_cancels_only_exact_unoffered_cp_without_allocator_or_cursor_rollback(self):
        f=self.fixture(mapping_fixture.MapperMappingTests);f.grant()
        draft=f.frame(PerceptionPipeline(f.fixture.calibration,f.profile.pipeline));f.mapping.accept(draft)
        hwm=f.db.connection.execute('SELECT allocator_highwater FROM storage_metadata WHERE singleton=1').fetchone()
        job=StorageJob(f.context,uid(),f.now,f.now+5_000_000)
        request=f.mapping.queue_checkpoint(job,now_us=f.now)
        cursor=f.mapping.collector._next;floor=f.mapping._cursor_floor;grant=f.mapping.collector.grant
        cells=dict(f.mapping.collector._cells)
        equal_copy=CheckpointRequest(request.job,request.payload)
        with self.assertRaises(MappingNotReady):f.mapping.cancel_unoffered_checkpoint(equal_copy,now_us=f.now+1)
        self.assertIs(f.mapping.pending.request,request);self.assertIsNone(f.mailbox.main_peek())
        f.mapping.cancel_unoffered_checkpoint(request,now_us=f.now+1)
        terminal=f.mapping.take_terminal()
        self.assertIs(terminal.request,request);self.assertIsInstance(terminal.result,StorageAbort)
        self.assertEqual(terminal.result.reason,'CANCELLED');self.assertFalse(terminal.current)
        self.assertIsNone(f.mapping.pending);self.assertIsNone(f.mapping.collector._pending)
        self.assertIsNone(f.mailbox.main_peek());self.assertIs(f.mapping.collector.grant,grant)
        self.assertEqual((f.mapping.collector._next,f.mapping._cursor_floor),(cursor,floor))
        self.assertEqual(f.mapping.collector._cells,cells)
        self.assertEqual(f.db.connection.execute('SELECT allocator_highwater FROM storage_metadata WHERE singleton=1').fetchone(),hwm)
        # 실제 offer 뒤 UNKNOWN은 여전히 원 mailbox에 있다. 취소나 새 UUID로 수습하지 않는다.
        other=f.mapping.queue_checkpoint(StorageJob(f.context,uid(),f.now+2,f.now+5_000_000),now_us=f.now+2)
        f.mapping.maintenance(now_us=f.now+2)
        dispatch=f.mailbox.main_take(current_context_sha256=f.context.sha256,now_us=f.now+3)
        self.assertIs(dispatch.request,other)
        unknown=CommitReceipt(f.context.database_uuid,other.job.job_id,other.digest,'UNKNOWN',None,None,f.context.sha256)
        self.assertTrue(f.mailbox.main_complete(dispatch,unknown,completed_us=f.now+4))
        self.assertFalse(f.mailbox.main_release(dispatch));f.mapping.maintenance(now_us=f.now+5)
        with self.assertRaises(MappingNotReady):f.mapping.cancel_unoffered_checkpoint(other,now_us=f.now+6)
        self.assertIs(f.mapping.pending.request,other);self.assertIs(f.mailbox.main_peek(),dispatch)
        self.assertIsNone(f.mapping.take_terminal())

    def test_catalog_queued_cancel_preserves_burned_ids_ranges_and_original_versions(self):
        f=self.fixture(catalog_fixture.MapperCatalogsTests);_,now=f.ready()
        job=StorageJob(f.owner.context,uid(),now,now+5_000_000)
        before=f.owner._accepted.parking._next_provenance
        request=f.owner.prepare_checkpoint(job,base_evidence=EvidenceTable(),now_us=now,durable_cutoff_pi_us=now+10)
        state=f.owner._accepted;cursor=state.parking._next_provenance;tracks=state.parking.catalog.tracks
        ranges=(state.lane.accepted_range,state.parking.catalog.accepted_range,state.parking._provenance)
        heads=state.parking._head_values;lane_ids=tuple(model.lane_ids for model in state.lane.models)
        self.assertGreater(cursor,before);self.assertIsNone(f.mailbox.main_peek())
        with self.assertRaises(CatalogNotReady):
            f.owner.cancel_unoffered_checkpoint(CheckpointRequest(job,request.payload),now_us=now+1)
        self.assertIs(state.parking._pending_checkpoint,request)
        f.owner.cancel_unoffered_checkpoint(request,now_us=now+1)
        self.assertIsNone(f.owner._queued);self.assertIsNone(state.parking._pending_checkpoint)
        self.assertIsNone(f.mailbox.main_peek());self.assertIs(f.owner._accepted,state)
        self.assertEqual(state.parking._next_provenance,cursor);self.assertEqual(state.parking.catalog.tracks,tracks)
        self.assertEqual((state.lane.accepted_range,state.parking.catalog.accepted_range,state.parking._provenance),ranges)
        self.assertEqual(state.parking._head_values,heads)
        self.assertEqual(tuple(model.lane_ids for model in state.lane.models),lane_ids)
        terminal=f.owner.take_terminal()
        self.assertIs(terminal.request,request);self.assertEqual(terminal.reason,'UNPUBLISHED_CANCELLED')

    def test_submitted_catalog_checkpoint_is_not_unpublished_even_before_main_takes_it(self):
        f=self.fixture(catalog_fixture.MapperCatalogsTests);_,now=f.ready()
        job=StorageJob(f.owner.context,uid(),now,now+5_000_000)
        request=f.owner.prepare_checkpoint(job,base_evidence=EvidenceTable(),now_us=now,durable_cutoff_pi_us=now+10)
        f.owner.maintenance(now_us=now)
        dispatch=f.mailbox.main_peek();state=f.owner._accepted;cursor=state.parking._next_provenance
        self.assertIs(dispatch.request,request);self.assertTrue(f.owner._submitted)
        with self.assertRaises(CatalogNotReady):f.owner.cancel_unoffered_checkpoint(request,now_us=now+1)
        self.assertIs(f.owner.pending.request,request);self.assertIs(f.mailbox.main_peek(),dispatch)
        self.assertIs(state.parking._pending_checkpoint,request)
        self.assertEqual(state.parking._next_provenance,cursor);self.assertIsNone(f.owner.take_terminal())

    def test_scene_descriptor_cancel_is_pre_adoption_only_and_unknown_retains_original_slot(self):
        f=self.fixture(scene_fixture.MapperSceneCheckpointTests);c=f.coordinator;now=f.f.now
        c.freeze_scene(f.job,current_tile_references=f.refs,current_hazard_revision=1,now_j_us=now)
        with self.assertRaises(SceneError):c.cancel_freeze_descriptor(replace(f.job),now_j_us=now+1)
        c.cancel_freeze_descriptor(f.job,now_j_us=now+1)
        self.assertIs(c.last_terminal.request,f.job);self.assertFalse(c.last_terminal.accepted)
        self.assertIsNone(c.last_terminal.receipt);self.assertIsNone(c._freeze_request)
        self.assertEqual(c.last_terminal.abort.reason,'CANCELLED')
        grant=decode(c.scene.grant_bytes,schema.SceneGrant);grant.lifecycle_revision+=1
        nonce=uid();grant.request_nonce=UUID(nonce).bytes
        self.assertTrue(c.maintenance(encode(grant),f.f.clock,now_j_us=now+2))
        job=StorageJob(f.f.context,uid(),now+3,1_500_000,nonce)
        descriptor=c.freeze_scene(job,current_tile_references=f.refs,current_hazard_revision=1,now_j_us=now+3)
        request=CheckpointRequest(job,CheckpointPayload.build(job,f.tiles,f.evidence,scene=descriptor,
            tile_references=f.refs,durable_cutoff_pi_us=1010002))
        c.adopt_freeze(request,current_tile_references=f.refs,current_hazard_revision=1,now_j_us=now+3,all_tiles=f.tiles)
        with self.assertRaises(SceneError):c.cancel_freeze_descriptor(job,now_j_us=now+4)
        unknown=CommitReceipt(job.context.database_uuid,job.job_id,request.digest,'UNKNOWN',None,None,job.context.sha256)
        self.assertFalse(c.complete_freeze(request,unknown,now_j_us=now+5))
        with self.assertRaises(SceneError):c.cancel_freeze_descriptor(job,now_j_us=job.expires_us+1)
        self.assertIs(c._freeze_request,request);self.assertFalse(c._freeze_terminal)
        self.assertEqual(c.operation_wait_reason,'WAIT_ORIGINAL_FREEZE_TERMINAL')
