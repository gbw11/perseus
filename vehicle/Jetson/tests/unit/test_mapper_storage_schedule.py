"""명시 dirty 일정과 실제 Mapping/mailbox/임시 SQLite의 작은 사건 순서 회귀다.

영상/peer는 기존 software fixture이며 실차/카메라/fsync 지연 수락 시험이 아니다.
catalog NEEDS 경계는 명시 입력하고 head/job은 실제 SQLite/owner 원본을 사용한다.
CP 실패 하나는 실제 commit 내부의 명시 fault injection이며 새 DB/권한 정책이 아니다.
"""

from dataclasses import replace
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_mapper_mapping as mapping_fixture
from xenova_vehicle.perception.pipeline import PerceptionPipeline
from xenova_vehicle.runtime.map_checkpoint import MapCheckpointNotReady
from xenova_vehicle.runtime.mapper_compute import MapperInput
from xenova_vehicle.runtime.mapper_storage_schedule import MapperStorageSchedule
from xenova_vehicle.runtime.mapper_storage import StorageAbort
from xenova_vehicle.storage.contracts import PersistenceConflict, StorageJob, CommitReceipt, canonical, json_bytes


@unittest.skipUnless(os.name == 'posix', '기존 실제 Mapping/SQLite fixture는 POSIX에서 검증함')
class MapperStorageScheduleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = mapping_fixture.MapperMappingTests(methodName='runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.grant()

    def accept_frame(self, sequence=12):
        fixture = self.fixture
        pipeline = PerceptionPipeline(fixture.fixture.calibration, fixture.profile.pipeline)
        if sequence == 12:
            draft = fixture.frame(pipeline)
        else:
            # 새 원 sequence의 software 관측이다. clock/pose/받은 시각을 새로 발급하지 않는다.
            item, observation = fixture.item()
            sample = replace(item.sample, rgb=replace(item.sample.rgb, sequence=sequence),
                depth=replace(item.sample.depth, sequence=sequence))
            item = MapperInput(item.job_id, item.source_generation, sample, item.expires_us,
                parser_cap_us=item.parser_cap_us, context_bytes=item.context_bytes)
            observation = replace(observation,
                rgb=replace(observation.rgb, stamp=replace(observation.rgb.stamp, sequence=sequence)),
                depth=replace(observation.depth, stamp=replace(observation.depth.stamp, sequence=sequence)))
            with patch.object(fixture, 'item', return_value=(item, observation)):
                draft = fixture.frame(pipeline)
        self.assertTrue(draft.ready, draft.reason)
        fixture.mapping.accept(draft)
        return draft

    def tick(self, schedule):
        fixture = self.fixture
        arguments = schedule.next_catalog_arguments(now_us=fixture.now)
        status = fixture.mapping.maintenance(now_us=fixture.now, catalog_arguments=arguments)
        return schedule.step(status, now_us=fixture.now)

    def due_checkpoint(self, schedule):
        self.assertEqual(self.tick(schedule), 'DIRTY_BATCH_WAIT')
        self.fixture.now += schedule.interval_us
        self.assertEqual(self.tick(schedule), 'CHECKPOINT_QUEUED')
        request = self.fixture.mapping.pending.request
        self.assertIsNone(self.fixture.mailbox.main_peek())
        self.tick(schedule)
        return request

    def complete_checkpoint(self, schedule):
        fixture = self.fixture
        dispatch = fixture.mailbox.main_take(current_context_sha256=fixture.context.sha256, now_us=fixture.now)
        request = dispatch.request
        receipt = fixture.store.commit(request.job, request.payload, current_context_sha256=fixture.context.sha256)
        self.assertEqual(receipt.status, 'COMMITTED')
        fixture.mailbox.main_complete(dispatch, receipt, completed_us=fixture.now)
        fixture.mailbox.main_release(dispatch)
        self.tick(schedule)
        self.assertIs(schedule.last_terminal.request, request)
        self.assertIs(schedule.last_terminal.result, receipt)
        self.assertTrue(schedule.last_terminal.current)
        # 최초 CP 뒤의 head는 원 owner가 발행한 실제 LOAD의 결과만 채운다.
        dispatch = fixture.mailbox.main_take(current_context_sha256=fixture.context.sha256, now_us=fixture.now)
        loaded = fixture.store.load(dispatch.request, current_context_sha256=fixture.context.sha256)
        fixture.mailbox.main_complete(dispatch, loaded, completed_us=fixture.now)
        fixture.mailbox.main_release(dispatch)
        self.tick(schedule)
        self.assertTrue(fixture.mapping._head_ready)
        self.assertIs(fixture.mapping.catalogs._head[0], loaded.heads)
        return request, receipt, loaded

    def test_explicit_interval_real_dirty_checkpoint_and_actual_load_head_do_not_repeat_clean_revision(self):
        fixture = self.fixture
        for invalid in (None, True, 0, -1, 5_000_001, 1.0):
            with self.subTest(interval=invalid), self.assertRaises(ValueError):
                MapperStorageSchedule(fixture.mapping, interval_us=invalid)
        calls = []
        def arguments():
            calls.append(fixture.now)
            return {}
        schedule = MapperStorageSchedule(fixture.mapping, interval_us=100, checkpoint_arguments=arguments)
        self.assertEqual(self.tick(schedule), 'IDLE')
        self.assertIsNone(fixture.mapping.pending)
        self.accept_frame()
        self.assertEqual(self.tick(schedule), 'DIRTY_BATCH_WAIT')
        fixture.now += 99
        self.assertEqual(self.tick(schedule), 'DIRTY_BATCH_WAIT')
        self.assertFalse(calls)
        fixture.now += 1
        self.assertEqual(self.tick(schedule), 'CHECKPOINT_QUEUED')
        original = fixture.mapping.pending.request
        self.assertIs(original.job.context, fixture.mapping._context)
        self.assertEqual(original.job.issued_us, fixture.now)
        self.assertEqual(original.job.expires_us, fixture.now + fixture.mapping._job_lifetime)
        self.assertEqual(calls, [fixture.now])
        self.tick(schedule)
        committed, _, _ = self.complete_checkpoint(schedule)
        self.assertIs(committed, original)
        fixture.now += 200
        self.assertEqual(self.tick(schedule), 'IDLE')
        self.assertIsNone(fixture.mapping.pending)
        self.assertEqual(len(calls), 1)

    def test_actual_failed_commit_is_sticky_and_preserves_ram_and_original_request(self):
        fixture = self.fixture
        self.accept_frame()
        accepted = fixture.mapping.collector
        schedule = MapperStorageSchedule(fixture.mapping, interval_us=1)
        original = self.due_checkpoint(schedule)
        dispatch = fixture.mailbox.main_take(current_context_sha256=fixture.context.sha256, now_us=fixture.now)
        with patch.object(fixture.store, '_write_payload', side_effect=PersistenceConflict('EXPLICIT_TEST_FAILURE')):
            receipt = fixture.store.commit(original.job, original.payload, current_context_sha256=fixture.context.sha256)
        self.assertEqual(receipt.status, 'FAILED')
        fixture.mailbox.main_complete(dispatch, receipt, completed_us=fixture.now)
        fixture.mailbox.main_release(dispatch)
        self.assertEqual(self.tick(schedule), 'ORIGINAL_STORAGE_TERMINAL')
        self.assertIs(schedule.last_terminal.request, original)
        self.assertIs(schedule.last_terminal.result, receipt)
        self.assertFalse(schedule.last_terminal.current)
        self.assertIs(fixture.mapping.collector, accepted)
        self.assertTrue(accepted._cells)
        fixture.now += 100
        self.assertEqual(self.tick(schedule), 'ORIGINAL_STORAGE_TERMINAL')
        self.assertIsNone(fixture.mapping.pending)
        self.assertIsNone(fixture.mailbox.main_peek())
        self.assertEqual(schedule.next_catalog_arguments(now_us=fixture.now), {})

    def test_not_ready_prepare_is_once_per_actual_accepted_revision(self):
        fixture = self.fixture
        self.accept_frame()
        schedule = MapperStorageSchedule(fixture.mapping, interval_us=1)
        self.assertEqual(self.tick(schedule), 'DIRTY_BATCH_WAIT')
        fixture.now += 1
        with patch.object(fixture.mapping, 'queue_checkpoint', side_effect=MapCheckpointNotReady('MISSING_ORIGINAL')) as prepare:
            self.assertEqual(self.tick(schedule), 'CHECKPOINT_REVISION_NOT_READY')
            for _ in range(4):
                self.assertEqual(self.tick(schedule), 'CHECKPOINT_REVISION_NOT_READY')
            self.assertEqual(prepare.call_count, 1)
            first_revision = fixture.mapping._revision
            self.accept_frame(sequence=13)
            self.assertGreater(fixture.mapping._revision, first_revision)
            self.assertEqual(self.tick(schedule), 'CHECKPOINT_REVISION_NOT_READY')
            self.assertEqual(prepare.call_count, 2)
        self.assertIsNone(fixture.mailbox.main_peek())
        self.assertIsNone(fixture.mapping.pending)

    def test_expired_actual_head_queues_one_original_load_without_fabricating_head(self):
        fixture = self.fixture
        self.accept_frame()
        schedule = MapperStorageSchedule(fixture.mapping, interval_us=1)
        self.due_checkpoint(schedule)
        self.complete_checkpoint(schedule)
        old = fixture.mapping.catalogs._head
        fixture.now = old[1].expires_us
        self.assertEqual(schedule.step('NEEDS_CURRENT_HEAD', now_us=fixture.now), 'HEAD_LOAD_QUEUED')
        request = fixture.mapping._head_load
        self.assertIs(request.job.context, fixture.mapping._context)
        self.assertEqual(request.checkpoint_ref, fixture.mapping._checkpoint_ref)
        self.assertEqual(request.job.issued_us, fixture.now)
        self.assertIs(fixture.mapping.catalogs._head, old)
        for _ in range(3):
            self.assertEqual(schedule.next_catalog_arguments(now_us=fixture.now), {})
            self.assertEqual(schedule.step('NEEDS_CURRENT_HEAD', now_us=fixture.now), 'HEAD_LOAD_QUEUED')
            self.assertIs(fixture.mapping._head_load, request)
        self.assertIsNone(fixture.mailbox.main_peek())

    def test_nonce_is_fixed_to_actual_head_and_parking_original_job_is_not_renewed(self):
        fixture = self.fixture
        head_job = StorageJob(fixture.context, str(uuid4()), fixture.now, fixture.now + 5_000_000)
        head = fixture.store.catalog_heads(head_job, current_context_sha256=fixture.context.sha256)
        fixture.mapping.catalogs.observe_heads(head, head_job, now_us=fixture.now)
        schedule = MapperStorageSchedule(fixture.mapping, interval_us=1)
        self.assertEqual(schedule.step('NEEDS_CURRENT_HEAD', now_us=fixture.now), 'NEEDS_CURRENT_HEAD')
        first = schedule.next_catalog_arguments(now_us=fixture.now)
        self.assertEqual(set(first), {'reservation_nonce'})
        for _ in range(3): self.assertEqual(schedule.next_catalog_arguments(now_us=fixture.now), first)
        self.assertIs(fixture.mapping.catalogs._head[0], head)
        # 아직 선택하지 않은 parking 분모는 별도 원 job/nonce로 처음 한 번만 만든다.
        self.assertEqual(schedule.step('NEEDS_EXCLUSIVE_PARKING_PROVENANCE', now_us=fixture.now),
            'NEEDS_EXCLUSIVE_PARKING_PROVENANCE')
        parking = schedule.next_catalog_arguments(now_us=fixture.now)
        self.assertEqual(set(parking), {'provenance_job', 'provenance_nonce'})
        job = parking['provenance_job']
        self.assertIs(job.context, fixture.mapping._context)
        for _ in range(3):
            again = schedule.next_catalog_arguments(now_us=fixture.now)
            self.assertIs(again['provenance_job'], job)
            self.assertEqual(again['provenance_nonce'], parking['provenance_nonce'])
        fixture.now = job.expires_us
        self.assertEqual(schedule.next_catalog_arguments(now_us=fixture.now), {})
        self.assertEqual(schedule.status, 'ORIGINAL_PARKING_PROVENANCE_EXPIRED')
        self.assertIs(schedule._provenance_job, job)
        self.assertIsNone(fixture.mapping.pending)
        self.assertIsNone(fixture.mailbox.main_peek())

    def next_publication(self):
        fixture = self.fixture
        binding = json_bytes(fixture.mapping._context.source_binding, 2048)
        binding['source_binding_revision'] = str(int(binding['source_binding_revision'])+1)
        updated = replace(fixture.mapping._context, source_binding=canonical(binding))
        fixture.port.publish(updated)
        return updated

    def test_scene_only_unoffered_checkpoint_retirement_keeps_dirty_and_does_not_poison_schedule(self):
        fixture = self.fixture
        self.accept_frame()
        schedule = MapperStorageSchedule(fixture.mapping, interval_us=1)
        self.due_checkpoint(schedule)
        self.complete_checkpoint(schedule)
        self.accept_frame(sequence=13)
        self.assertEqual(self.tick(schedule), 'DIRTY_BATCH_WAIT')
        fixture.now += 1
        self.assertEqual(self.tick(schedule), 'CHECKPOINT_QUEUED')
        original = fixture.mapping.pending.request
        self.assertIs(fixture.mapping.catalogs._queued.request, original)
        self.assertIsNone(fixture.mailbox.main_peek())
        saved, committed, dirty = schedule._saved_revision, schedule._last_commit_us, schedule._dirty_since
        updated = self.next_publication()
        self.assertEqual(schedule.next_catalog_arguments(now_us=fixture.now), {})
        status = self.tick(schedule)
        self.assertNotEqual(status, 'ORIGINAL_STORAGE_TERMINAL')
        self.assertIsNone(schedule._fault)
        self.assertEqual(status, 'WAIT_ORIGINAL_CONTEXT_TERMINAL')
        # Catalog가 원 미게시 CP를 닫은 뒤 다음 owner 유지 단계에서 새 context를 결박한다.
        self.tick(schedule)
        self.assertIs(fixture.mapping._context, updated)
        self.assertIs(schedule.last_terminal.request, original)
        self.assertEqual(schedule.last_terminal.result, StorageAbort('SOURCE_REPLACED'))
        self.assertFalse(schedule.last_terminal.current)
        self.assertEqual((schedule._saved_revision, schedule._last_commit_us, schedule._dirty_since),
            (saved, committed, dirty))
        self.assertIsNone(schedule._checkpoint)
        self.assertIsNot(fixture.mapping.pending.request if fixture.mapping.pending is not None else None, original)
        self.assertIsNot(original.job.context, updated)

    def test_scene_transition_suppresses_old_selected_arguments_then_drops_only_unused_choices(self):
        fixture = self.fixture
        schedule = MapperStorageSchedule(fixture.mapping, interval_us=1)
        job = StorageJob(fixture.context, str(uuid4()), fixture.now, fixture.now+5_000_000)
        head = fixture.store.catalog_heads(job, current_context_sha256=fixture.context.sha256)
        fixture.mapping.catalogs.observe_heads(head, job, now_us=fixture.now)
        schedule.step('NEEDS_CURRENT_HEAD', now_us=fixture.now)
        old_range = schedule.next_catalog_arguments(now_us=fixture.now)
        self.assertEqual(set(old_range), {'reservation_nonce'})
        schedule.step('NEEDS_EXCLUSIVE_PARKING_PROVENANCE', now_us=fixture.now)
        old_provenance = schedule.next_catalog_arguments(now_us=fixture.now)
        old_job = old_provenance['provenance_job']
        updated = self.next_publication()
        self.assertEqual(schedule.next_catalog_arguments(now_us=fixture.now), {})
        self.assertIs(schedule._provenance_job, old_job)
        self.assertIs(schedule._range_head[1], job)
        self.tick(schedule)
        self.assertIs(fixture.mapping._context, updated)
        self.assertIsNone(schedule._range_head)
        self.assertIsNone(schedule._provenance_job)
        self.assertIsNone(schedule._fault)
        schedule.step('NEEDS_EXCLUSIVE_PARKING_PROVENANCE', now_us=fixture.now)
        newer = schedule.next_catalog_arguments(now_us=fixture.now)
        self.assertIs(newer['provenance_job'].context, updated)
        self.assertNotEqual(newer['provenance_nonce'], old_provenance['provenance_nonce'])
        self.assertEqual(old_job.context, fixture.context)
        self.assertEqual(old_job.expires_us, old_job.issued_us+fixture.mapping._job_lifetime)

    def test_unknown_pending_and_prior_real_failures_remain_sticky_across_source_retirement(self):
        fixture = self.fixture
        self.accept_frame()
        schedule = MapperStorageSchedule(fixture.mapping, interval_us=1)
        original = self.due_checkpoint(schedule)
        dispatch = fixture.mailbox.main_take(current_context_sha256=fixture.context.sha256, now_us=fixture.now)
        unknown = CommitReceipt(fixture.context.database_uuid, original.job.job_id, original.digest,
            'UNKNOWN', None, None, fixture.context.sha256)
        fixture.mailbox.main_complete(dispatch, unknown, completed_us=fixture.now)
        self.assertFalse(fixture.mailbox.main_release(dispatch))
        self.tick(schedule)
        self.next_publication()
        self.assertEqual(schedule.next_catalog_arguments(now_us=fixture.now), {})
        self.tick(schedule)
        self.assertIs(fixture.mapping.pending.request, original)
        self.assertIs(fixture.mailbox.main_peek(), dispatch)
        self.assertIs(schedule._checkpoint, original)
        self.assertFalse(fixture.mailbox.main_release(dispatch))
        failed = replace(unknown, status='FAILED')
        for result in (unknown, failed, StorageAbort('EXPIRED'), StorageAbort('OWNER_EXITED')):
            # 명시한 원 terminal의 일정 분류 회귀다. UNKNOWN의 실제 mailbox 소유권은 위에서 유지한다.
            isolated = MapperStorageSchedule(fixture.mapping, interval_us=1)
            self.assertEqual(isolated._terminal(original, result, False, fixture.now, 'MAPPING_TERMINAL'),
                'ORIGINAL_STORAGE_TERMINAL')
            fault = isolated._fault
            isolated._terminal(original, StorageAbort('SOURCE_REPLACED'), False, fixture.now, 'MAPPING_TERMINAL')
            self.assertEqual(isolated._fault, fault)
            self.assertEqual(isolated.next_catalog_arguments(now_us=fixture.now), {})
            self.assertEqual(isolated._saved_revision, 0)
            self.assertIsNone(isolated._last_commit_us)
