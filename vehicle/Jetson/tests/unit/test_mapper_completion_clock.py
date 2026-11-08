"""두 compute owner가 원 encode 완료 clock과 정확한 만료 경계를 보존하는 회귀다.

기존 임시 SQLite/raw/보정/source fixture와 실제 mailbox·copy encoder를 사용한다.
clock_us는 명시 software clock이며 결과·encoder·시간 함수를 patch하지 않는다.
이 시험의 CopyLease는 local codec 입력이다. native arena 취득/반납 또는 현재 지도
승인을 만들지 않으며 fullflow integration rig에는 의존하지 않는다.
"""

from hashlib import sha256
import os
import unittest
from uuid import uuid4

import test_mapper_mapping as mapping_fixture
from xenova_vehicle.runtime.mapper_catalogs import MapperCatalogs
from xenova_vehicle.runtime.mapper_mapping import MapperMapping
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox
from xenova_vehicle.runtime.raw_leases import Endpoint
from xenova_vehicle.runtime.storage_copy import CopyLease
from xenova_vehicle.storage.contracts import StorageJob
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import CheckpointPayload


def uid():
    return str(uuid4())


@unittest.skipUnless(os.name == 'posix', 'actual temporary SQLite/writer fixture requires POSIX')
class MapperCompletionClockTests(unittest.TestCase):
    def setUp(self):
        self.base = mapping_fixture.MapperMappingTests(methodName='runTest')
        self.addCleanup(self.base.doCleanups)
        self.base.setUp()

    def original_encode(self):
        base = self.base
        context, now = base.context, base.now
        expires = now + 100_000
        job = StorageJob(context, uid(), now, expires)
        cutoff = base.inputs.clock_lease.j_interval_to_pi(base.inputs.pose.captured_us, now)[1]
        request = CheckpointRequest(job, CheckpointPayload.build(job, durable_cutoff_pi_us=cutoff))
        mailbox = MapperStorageMailbox(context.sha256)
        self.assertTrue(job.current(context.sha256, now))
        self.assertTrue(mailbox.compute_offer(request, now_us=now))
        dispatch = mailbox.main_take(current_context_sha256=context.sha256, now_us=now)
        self.assertIs(dispatch.request, request)
        writer = CopyLease(base.fixture.raw.fields['service_instance'], uid(), 1,
            Endpoint(uid(), context.mapper_worker), Endpoint(uid(), base.material[0]['storage_worker']), uid(),
            raw_binding_sha256=base.fixture.raw.sha256,
            publication_binding_sha256=sha256(context.source_binding).hexdigest())
        original = mailbox.main_request_encode(dispatch, writer, issued_us=now, expires_us=expires)
        self.assertIs(original.dispatch, dispatch)
        self.assertEqual(original.expires_us, expires)
        return mailbox, request, dispatch, original

    def assert_exact_expiry(self, owner, mailbox, request, dispatch, original):
        expires = request.job.expires_us
        owner.maintenance(now_us=self.base.now)
        result = mailbox.main_take_encoding(dispatch)
        self.assertIsNotNone(result)
        self.assertIs(result.request, original)
        self.assertEqual(result.completed_us, expires)
        self.assertEqual(result.error, 'STALE_OR_EXPIRED')
        self.assertIsNone(result.encoding)
        self.assertEqual(request.job.expires_us, expires)
        self.assertEqual(original.expires_us, expires)
        self.assertIs(mailbox.main_peek(), dispatch)

    def test_mapping_owner_rejects_encode_completed_at_original_exact_expiry(self):
        base = self.base
        mailbox, request, dispatch, original = self.original_encode()
        owner = MapperMapping(base.port, mailbox, base.fixture.raw, base.fixture.calibration, base.profile,
            initial_storage_material=base.material, clock_us=lambda: request.job.expires_us)
        self.assert_exact_expiry(owner, mailbox, request, dispatch, original)

    def test_catalog_owner_rejects_encode_completed_at_original_exact_expiry(self):
        base = self.base
        mailbox, request, dispatch, original = self.original_encode()
        owner = MapperCatalogs(base.context, mailbox=mailbox, clock_id=base.fixture.raw.fields['clock_epoch'],
            map_frame_id='odom:' + base.context.odom_epoch, body=base.fixture.calibration.body,
            method_manifest_sha256=base.profile.sha256, input_kind='LIVE',
            calibration_profile=base.fixture.calibration, clock_us=lambda: request.job.expires_us)
        self.assert_exact_expiry(owner, mailbox, request, dispatch, original)
