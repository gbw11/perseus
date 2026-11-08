"""고정 한 D 작업 경로의 원 참조·source fence·실제 main 반납 분리를 검사한다.

SQL/actor/arena를 실행하지 않는 사건 순서 시험이다. 합성 receipt는 내구 저장이나
native 메모리 반환 증거가 아니며 main_release의 외부 사실을 대신하지 않는다.
"""

from dataclasses import replace
import unittest
from unittest.mock import patch

from test_parking_catalog import context, JOB, FIXED
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox, StorageAbort
from xenova_vehicle.storage.contracts import StorageJob, CommitReceipt
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import CheckpointPayload


def request(number=0):
    job = StorageJob(context(), JOB if not number else '20000000-0000-4000-8000-%012d' % (10 + number), 100, 200)
    return CheckpointRequest(job, CheckpointPayload.build(job, durable_cutoff_pi_us=1))


def receipt(original, status='COMMITTED'):
    return CommitReceipt(original.job.context.database_uuid, original.job.job_id, original.digest, status,
        1 if status == 'COMMITTED' else None, 150 if status == 'COMMITTED' else None, original.job.context.sha256)


class MapperStorageTests(unittest.TestCase):
    def test_one_original_request_holds_until_compute_receives_and_main_really_releases(self):
        original = request(); mailbox = MapperStorageMailbox(original.job.context.sha256)
        self.assertTrue(mailbox.compute_offer(original, now_us=110))
        self.assertTrue(mailbox.compute_offer(original, now_us=110))
        self.assertFalse(mailbox.compute_offer(request(1), now_us=110))
        peek = mailbox.main_peek()
        self.assertIs(peek.request, original)
        self.assertFalse(mailbox.main_release(peek))
        dispatch = mailbox.main_take(current_context_sha256=original.job.context.sha256, now_us=120)
        self.assertIs(dispatch, peek)
        result = receipt(original)
        self.assertTrue(mailbox.main_complete(dispatch, result, completed_us=160))
        delivered = mailbox.compute_take(current_context_sha256=original.job.context.sha256, now_us=170)
        self.assertTrue(delivered.current)
        self.assertIs(delivered.request, original)
        self.assertIs(delivered.result, result)
        self.assertFalse(mailbox.compute_offer(request(1), now_us=170))
        self.assertTrue(mailbox.main_release(dispatch))
        self.assertTrue(mailbox.compute_offer(request(1), now_us=170))

    def test_source_fence_drops_only_unstarted_and_keeps_original_refs_historical(self):
        original = request(); mailbox = MapperStorageMailbox(original.job.context.sha256)
        mailbox.compute_offer(original, now_us=110)
        self.assertIs(mailbox.fence('d' * 64, now_us=120), original)
        self.assertIsNone(mailbox.main_take(current_context_sha256='d' * 64, now_us=130))
        delivery = mailbox.compute_take(current_context_sha256='d' * 64, now_us=130)
        self.assertFalse(delivery.current)
        self.assertIs(delivery.request, original)
        self.assertIsInstance(delivery.result, StorageAbort)
        self.assertIsNone(mailbox.main_peek())

    def test_inflight_source_replacement_does_not_free_copy_or_apply_old_success(self):
        original = request(); mailbox = MapperStorageMailbox(original.job.context.sha256)
        mailbox.compute_offer(original, now_us=110)
        dispatch = mailbox.main_take(current_context_sha256=original.job.context.sha256, now_us=120)
        self.assertIsNone(mailbox.fence('d' * 64, now_us=130))
        result = receipt(original)
        mailbox.main_complete(dispatch, result, completed_us=160)
        delivery = mailbox.compute_take(current_context_sha256='d' * 64, now_us=170)
        self.assertFalse(delivery.current)
        self.assertIs(delivery.result, result)
        self.assertIs(mailbox.main_peek(), dispatch)
        self.assertTrue(mailbox.main_release(dispatch))

    def test_unstarted_expiry_never_reaches_actor_and_same_source_renewal_is_noop(self):
        original = request(); mailbox = MapperStorageMailbox(original.job.context.sha256)
        mailbox.compute_offer(original, now_us=110)
        self.assertIsNone(mailbox.fence(original.job.context.sha256, now_us=120))
        self.assertIsNone(mailbox.main_take(current_context_sha256=original.job.context.sha256, now_us=200))
        delivery = mailbox.compute_take(current_context_sha256=original.job.context.sha256, now_us=200)
        self.assertFalse(delivery.current)
        self.assertEqual(delivery.result.reason, 'EXPIRED')
        self.assertIsNone(mailbox.main_peek())

    def test_old_handle_and_late_callback_cannot_release_or_replace_the_next_job(self):
        original = request(); mailbox = MapperStorageMailbox(original.job.context.sha256)
        mailbox.compute_offer(original, now_us=110)
        old = mailbox.main_take(current_context_sha256=original.job.context.sha256, now_us=120)
        mailbox.main_complete(old, receipt(original), completed_us=160)
        mailbox.main_release(old); mailbox.compute_take(current_context_sha256=original.job.context.sha256, now_us=170)
        newer = request(1); mailbox.compute_offer(newer, now_us=170)
        self.assertFalse(mailbox.main_release(old))
        self.assertFalse(mailbox.main_complete(old, receipt(original), completed_us=180))
        self.assertIs(mailbox.main_peek().request, newer)

    def test_unknown_is_pinned_until_exact_resolution_and_actual_main_release(self):
        original = request(); mailbox = MapperStorageMailbox(original.job.context.sha256)
        mailbox.compute_offer(original, now_us=110)
        dispatch = mailbox.main_take(current_context_sha256=original.job.context.sha256, now_us=120)
        mailbox.main_release(dispatch)  # view는 먼저 끝나도 논리 UNKNOWN은 별도로 유지한다.
        unknown = receipt(original, 'UNKNOWN')
        mailbox.main_complete(dispatch, unknown, completed_us=160)
        self.assertFalse(mailbox.compute_take(current_context_sha256=original.job.context.sha256, now_us=170).current)
        self.assertFalse(mailbox.main_release(dispatch))
        self.assertFalse(mailbox.compute_offer(request(1), now_us=170))
        known = receipt(original)
        self.assertTrue(mailbox.main_resolve(dispatch, known, completed_us=210))
        delivery = mailbox.compute_take(current_context_sha256=original.job.context.sha256, now_us=220)
        self.assertFalse(delivery.current)
        self.assertIs(delivery.result, known)
        self.assertTrue(mailbox.main_release(dispatch))

    def test_hash_json_validation_is_outside_reference_swap_locks(self):
        original = request(); mailbox = MapperStorageMailbox(original.job.context.sha256)
        from xenova_vehicle.storage import contracts
        actual = contracts.canonical
        def guarded(value):
            self.assertFalse(mailbox._lock.locked())
            return actual(value)
        with patch.object(contracts, 'canonical', side_effect=guarded):
            mailbox.compute_offer(original, now_us=110)
            dispatch = mailbox.main_take(current_context_sha256=original.job.context.sha256, now_us=120)
            result = receipt(original)
            mailbox.main_complete(dispatch, result, completed_us=160)
            self.assertTrue(mailbox.compute_take(current_context_sha256=original.job.context.sha256, now_us=170).current)
            mailbox.main_release(dispatch)

    def test_wrong_job_and_future_completion_never_become_current(self):
        original = request(); mailbox = MapperStorageMailbox(original.job.context.sha256)
        mailbox.compute_offer(original, now_us=110)
        dispatch = mailbox.main_take(current_context_sha256=original.job.context.sha256, now_us=120)
        with self.assertRaises(ValueError):
            mailbox.main_complete(dispatch, receipt(request(1)), completed_us=160)
        result = receipt(original)
        mailbox.main_complete(dispatch, result, completed_us=190)
        self.assertFalse(mailbox.compute_take(current_context_sha256=original.job.context.sha256, now_us=170).current)

    def test_wire_range_preserves_actual_bytes_and_outer_time_without_a_commit_stamp(self):
        from envelope_fixtures import fields
        from xenova_vehicle.runtime.local_envelope import encode_structure
        from xenova_vehicle.runtime.storage_messages import DbMessage
        from xenova_vehicle.runtime.parking_bridge import ParkingBridge, WireProvenanceGrant
        from xenova_vehicle.storage.contracts import ProvenanceRequest, canonical
        from xenova_vehicle.perception.calibration import BodyModel
        from test_parking import CLOCK, FRAME
        job = request().job
        bridge = ParkingBridge(job.context, clock_id=CLOCK, map_frame_id=FRAME,
            body=BodyModel(), method_manifest_sha256='c' * 64, input_kind='SYNTHETIC')
        original = bridge.prepare_provenance(job, '20000000-0000-4000-8000-000000000080', block_count=8)
        body = dict(database_uuid=job.context.database_uuid, mapper_worker=job.context.mapper_worker,
            reservation_nonce=original.reservation_nonce, start_inclusive='1', end_exclusive='9',
            highwater_after='9', result='GRANTED')
        # 등록 peer/sequence 검사는 main의 별도 책임이다. 여기서는 합성 원 packet만 전달한다.
        outer = fields('DB_JOB', payload=dict(op='RANGE_RESULT', body=body), domain='STORAGE')
        outer.update(issued_j_us='150', expires_j_us='200')
        packet = encode_structure(outer)
        result = DbMessage(packet, 'RANGE_RESULT', canonical(body), 150, 200)
        mailbox = MapperStorageMailbox(job.context.sha256)
        mailbox.compute_offer(original, now_us=110)
        dispatch = mailbox.main_take(current_context_sha256=job.context.sha256, now_us=120)
        mailbox.main_complete(dispatch, result, completed_us=160)
        delivery = mailbox.compute_take(current_context_sha256=job.context.sha256, now_us=170)
        self.assertTrue(delivery.current)
        self.assertIs(delivery.result, result)
        grant = delivery.admitted_result
        self.assertIs(type(grant), WireProvenanceGrant)
        self.assertEqual((grant.reply_bytes, grant.issued_us, grant.received_us), (packet.raw_bytes, 150, 160))
        self.assertFalse(hasattr(grant, 'committed_j_us'))
        self.assertTrue(bridge.accept_wire_grant(original, grant, now_us=170))
        self.assertFalse(bridge.accept_wire_grant(original, grant, now_us=170))
        mailbox.main_release(dispatch)

    def test_local_unstarted_abort_closes_only_original_provenance_wait_without_issuing_ids(self):
        from xenova_vehicle.runtime.parking_bridge import ParkingBridge
        from xenova_vehicle.perception.calibration import BodyModel
        from test_parking import CLOCK, FRAME
        job = request().job
        bridge = ParkingBridge(job.context, clock_id=CLOCK, map_frame_id=FRAME,
            body=BodyModel(), method_manifest_sha256='c' * 64, input_kind='SYNTHETIC')
        original = bridge.prepare_provenance(job, '20000000-0000-4000-8000-000000000080', block_count=8)
        with self.assertRaises(ValueError):
            bridge.retire_storage_abort(original, StorageAbort('EXPIRED'), now_us=199)
        self.assertTrue(bridge.retire_storage_abort(original, StorageAbort('EXPIRED'), now_us=200))
        self.assertEqual(bridge.catalog.available_ids, 0)
        replacement = bridge.prepare_provenance(request(1).job,
            '20000000-0000-4000-8000-000000000081', block_count=8)
        self.assertIsNot(replacement, original)

    def test_local_calibration_profile_and_whole_vehicle_bundle_remain_distinct(self):
        from xenova_vehicle.runtime.parking_bridge import ParkingBridge
        from xenova_vehicle.perception.calibration import CalibrationProfile, synthetic_calibration
        from test_parking import CLOCK, FRAME, batch
        job = request().job
        fields = synthetic_calibration().to_dict()
        fields['vehicle_bundle_sha256'] = job.context.calibration
        calibration = CalibrationProfile.from_dict(fields)
        self.assertNotEqual(calibration.sha256, job.context.calibration)
        bridge = ParkingBridge(job.context, clock_id=CLOCK, map_frame_id=FRAME,
            body=calibration.body, method_manifest_sha256='c' * 64, input_kind='SYNTHETIC',
            calibration_profile=calibration)
        self.assertEqual(bridge.local_calibration_id, calibration.sha256)
        self.assertEqual(bridge.context.calibration, fields['vehicle_bundle_sha256'])
        value = batch()
        local_source = replace(value.source, calibration_id=calibration.sha256)
        # catalog도 내부 map의 원 profile을 검사하며 whole hash를 내부 source에 덮어쓰지 않는다.
        local_batch = replace(value, source=local_source,
            candidates=tuple(replace(candidate, source=local_source) for candidate in value.candidates))
        bridge.catalog.associate(local_batch, FIXED, prior_qualified=True)
        with self.assertRaises(ValueError):
            bridge.catalog.associate(value, FIXED, prior_qualified=True)
        with self.assertRaises(ValueError):
            ParkingBridge(job.context, clock_id=CLOCK, map_frame_id=FRAME, body=calibration.body,
                method_manifest_sha256='c' * 64, input_kind='LIVE')

    def test_main_passes_owned_copy_without_decoding_and_compute_validates_original_load_outside_lock(self):
        from hashlib import sha256
        from xenova_vehicle.runtime.storage_copy import CopyLease, OwnedCopy, encode_load
        from xenova_vehicle.runtime.raw_leases import Endpoint
        from xenova_vehicle.storage.contracts import LoadRequest, CatalogHeadSnapshot, canonical
        from xenova_vehicle.storage.records import LoadedCheckpoint
        cp = request(); c = cp.job.context
        reference = dict(checkpoint_job_id=cp.job.job_id, durable_revision='1',
            manifest_sha256=sha256(cp.payload.manifest).hexdigest(), manifest_length=len(cp.payload.manifest),
            tile_count=0, map_epoch=c.map_epoch, frame_epoch=c.frame_epoch, odom_epoch=c.odom_epoch,
            pi_host_boot=c.pi_host_boot, configuration=c.configuration, calibration=c.calibration,
            primitive_sha256=c.primitive_sha256, origin_mapper_instance=c.origin_mapper_instance)
        original = LoadRequest(request(1).job, canonical(reference), canonical(dict(kind='CHECKPOINT_PAGE', cursor=0)))
        heads = CatalogHeadSnapshot(original.job.job_id, c.sha256, c.database_uuid, c.map_epoch,
            (('LANE', 1, 0), ('SLOT', 1, 0)), 150, 200)
        loaded = LoadedCheckpoint(original, cp.job, original.checkpoint_ref, cp.payload, heads, 150)
        def uid(n): return '20000000-0000-4000-8000-%012d' % n
        lease = CopyLease(uid(80), uid(81), 1, Endpoint(uid(82), uid(83)), Endpoint(uid(84), uid(85)), uid(86))
        encoded = encode_load(loaded, lease); read = replace(lease, read_token=uid(87))
        ready = dict(encoded.ready, copy_lease=read.fields)
        owned = OwnedCopy(read, canonical(ready), encoded.evidence, encoded.descriptors, encoded.manifest, encoded.tiles)
        mailbox = MapperStorageMailbox(c.sha256); mailbox.compute_offer(original, now_us=110)
        handle = mailbox.main_take(current_context_sha256=c.sha256, now_us=120)
        with patch.object(OwnedCopy, 'decode', side_effect=AssertionError('main must not decode')):
            self.assertTrue(mailbox.main_complete(handle, owned, completed_us=160))
            self.assertTrue(mailbox.main_release(handle))
        actual = OwnedCopy.decode
        def decode_on_compute(value):
            self.assertFalse(mailbox._lock.locked())
            return actual(value)
        with patch.object(OwnedCopy, 'decode', decode_on_compute):
            delivered = mailbox.compute_take(current_context_sha256=c.sha256, now_us=170)
        self.assertTrue(delivered.current); self.assertIs(delivered.result, owned)
        self.assertIs(delivered.admitted_result.request, original)
        self.assertEqual(delivered.admitted_result.payload.manifest, cp.payload.manifest)
        self.assertIsNone(mailbox.main_peek())

    def test_original_db_abort_retires_unknown_without_making_a_wire_result_or_current_success(self):
        from xenova_vehicle.runtime.resident_db import DbAbort
        from xenova_vehicle.runtime.raw_leases import Endpoint
        from xenova_vehicle.runtime.storage_messages import prepare_payload
        from xenova_vehicle.storage.contracts import canonical
        original = request(); c = original.job.context; mailbox = MapperStorageMailbox(c.sha256)
        mailbox.compute_offer(original, now_us=110)
        handle = mailbox.main_take(current_context_sha256=c.sha256, now_us=120)
        mailbox.main_complete(handle, receipt(original, 'UNKNOWN'), completed_us=160)
        self.assertFalse(mailbox.compute_take(current_context_sha256=c.sha256, now_us=170).current)
        mapper = Endpoint('20000000-0000-4000-8000-000000000091', c.mapper_worker)
        notice = DbAbort('CHECKPOINT', canonical(prepare_payload(original)['body']), original.job.job_id,
            100, 200, 180, mapper, c.sha256, 'D_OWNER_REBOUND_UNKNOWN')
        wrong = replace(notice, mapper=Endpoint(mapper.channel_epoch, '20000000-0000-4000-8000-000000000092'))
        with self.assertRaises(ValueError): mailbox.main_abort(handle, wrong)
        self.assertTrue(mailbox.main_abort(handle, notice))
        self.assertTrue(mailbox.main_abort(handle, notice))
        delivered = mailbox.compute_take(current_context_sha256=c.sha256, now_us=190)
        self.assertFalse(delivered.current)
        self.assertIs(type(delivered.result), StorageAbort)
        self.assertIs(delivered.result.original_notice, notice)
        self.assertTrue(mailbox.main_release(handle))
        self.assertIsNone(mailbox.main_peek())
        # 역사 조회는 새 transport UUID/기한을 가지지만 원 job의 기한/성공을 되살리지 않는다.
        second = MapperStorageMailbox(c.sha256); second.compute_offer(original, now_us=110)
        handle = second.main_take(current_context_sha256=c.sha256, now_us=120)
        second.main_complete(handle, receipt(original, 'UNKNOWN'), completed_us=160)
        query_id = '20000000-0000-4000-8000-000000000093'
        query = DbAbort('QUERY', canonical(dict(database_uuid=c.database_uuid, job_id=original.job.job_id,
            job_digest=original.digest)), query_id, 210, 300, 220, mapper, c.sha256, 'D_OWNER_REBOUND_UNKNOWN')
        self.assertTrue(second.main_abort(handle, query))
        history = second.compute_take(current_context_sha256=c.sha256, now_us=230)
        self.assertFalse(history.current)
        self.assertEqual(history.result.original_notice.request_id, query_id)
        self.assertEqual(history.request.job.expires_us, 200)
        self.assertTrue(second.main_release(handle))


if __name__ == '__main__':
    unittest.main()
