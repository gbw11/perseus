"""독립 Catalog 완료가 Mapping의 물리 반환 대기에 막히지 않는지 검사한다.

원 context/provenance 및 LOAD는 기존 실제 임시 SQLite fixture를 사용한다. mailbox의
완료/소비/반납 순서를 검사하며 SHM/token/native 반환이나 World 권한을 주장하지 않는다.
UNKNOWN은 원 요청에 결박한 명시 불확정 결과이며 내구 성공 receipt가 아니다.
"""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import test_mapper_mapping as fixtures
from xenova_vehicle.storage.contracts import StorageJob,LoadRequest,CommitReceipt,canonical
from xenova_vehicle.storage.records import CheckpointPayload


@unittest.skipUnless(os.name=='posix','actual SQLite owner fixture requires POSIX')
class MappingCatalogDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.MapperMappingTests(methodName='runTest')
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp();self.fixture.grant()
        self.mapping=self.fixture.mapping;self.mailbox=self.fixture.mailbox
        self.context=self.fixture.context;self.now=self.fixture.now
        job=StorageJob(self.context,str(uuid4()),self.now,self.now+5_000_000)
        payload=CheckpointPayload.build(job,durable_cutoff_pi_us=1)
        receipt=self.fixture.store.commit(job,payload,current_context_sha256=self.context.sha256)
        self.assertEqual(receipt.status,'COMMITTED')
        self.reference=self.fixture.store.latest_complete(expires_us=self.now+5_000_000)
        self.assertIsNotNone(self.reference)

    def request(self):
        job=StorageJob(self.context,str(uuid4()),self.now,self.now+1000)
        return LoadRequest(job,canonical(self.reference),canonical(dict(kind='CHECKPOINT_PAGE',cursor=0)))

    def offered(self):
        request=self.request()
        self.assertTrue(self.mapping.catalogs.queue_load(request))
        self.assertEqual(self.mapping.maintenance(now_us=self.now),'OFFERED')
        self.assertIsNone(self.mapping.pending)
        self.assertIs(self.mapping.catalogs.pending.request,request)
        dispatch=self.mailbox.main_take(current_context_sha256=self.context.sha256,now_us=self.now)
        self.assertIs(dispatch.request,request)
        loaded=self.fixture.store.load(request,current_context_sha256=self.context.sha256)
        self.assertTrue(self.mailbox.main_complete(dispatch,loaded,completed_us=self.now))
        return request,dispatch,loaded

    def test_released_original_catalog_load_is_consumed_and_delivery_stays_with_original_owner(self):
        request,dispatch,loaded=self.offered()
        self.assertTrue(self.mailbox.main_release(dispatch))
        self.assertIs(self.mailbox.main_peek(),dispatch)
        # 종전 코드는 이 지점에서 WAIT_PHYSICAL_RELEASE만 반환해 compute_take를 막았다.
        self.assertEqual(self.mapping.maintenance(now_us=self.now),'IDLE')
        self.assertIsNone(self.mailbox.main_peek())
        self.assertIsNone(self.mapping.catalogs.pending)
        self.assertEqual(self.mapping.catalogs._head[0],loaded.heads)
        delivery=self.mapping.catalogs.take_delivery()
        self.assertIs(delivery.request,request)
        self.assertIs(delivery.admitted_result,loaded)
        self.assertTrue(delivery.current)
        self.assertIsNone(self.mapping.catalogs.take_delivery())

    def test_semantic_consumption_does_not_replace_actual_main_release(self):
        request,dispatch,loaded=self.offered()
        self.assertEqual(self.mapping.maintenance(now_us=self.now),'WAIT_PHYSICAL_RELEASE')
        self.assertIs(self.mailbox.main_peek(),dispatch)
        self.assertIsNone(self.mapping.catalogs.pending)
        self.assertIs(self.mapping.catalogs.take_delivery().request,request)
        self.assertEqual(self.mapping.maintenance(now_us=self.now),'WAIT_PHYSICAL_RELEASE')
        self.assertTrue(self.mailbox.main_release(dispatch))
        self.assertIsNone(self.mailbox.main_peek())
        self.assertEqual(self.mapping.maintenance(now_us=self.now),'IDLE')

    def test_other_owner_unknown_keeps_original_mailbox_and_does_not_run_catalog_consumer(self):
        queued=self.request();self.assertTrue(self.mapping.catalogs.queue_load(queued))
        other=self.request();self.assertTrue(self.mailbox.compute_offer(other,now_us=self.now))
        dispatch=self.mailbox.main_take(current_context_sha256=self.context.sha256,now_us=self.now)
        unknown=CommitReceipt(self.context.database_uuid,other.job.job_id,other.digest,
            'UNKNOWN',None,None,self.context.sha256)
        self.assertTrue(self.mailbox.main_complete(dispatch,unknown,completed_us=self.now))
        self.assertFalse(self.mailbox.main_release(dispatch))
        with patch.object(self.mapping.catalogs,'maintenance',side_effect=AssertionError('foreign head has another owner')):
            self.assertEqual(self.mapping.maintenance(now_us=self.now),'WAIT_PHYSICAL_RELEASE')
        self.assertIs(self.mailbox.main_peek(),dispatch)
        self.assertIs(dispatch.request,other)
        self.assertIs(self.mapping.catalogs._queued.request,queued)
        self.assertEqual((other.job.issued_us,other.job.expires_us),(self.now,self.now+1000))

    def test_late_original_catalog_load_is_historical_and_never_refreshes_heads(self):
        request,dispatch,loaded=self.offered()
        self.assertTrue(self.mailbox.main_release(dispatch))
        self.fixture.now=request.job.expires_us
        self.assertEqual(self.mapping.maintenance(now_us=self.fixture.now),'IDLE')
        delivery=self.mapping.catalogs.take_delivery()
        self.assertIs(delivery.request,request)
        self.assertIs(delivery.admitted_result,loaded)
        self.assertFalse(delivery.current)
        self.assertEqual(delivery.reason,'HISTORICAL_OR_UNCONFIRMED')
        self.assertIsNone(self.mapping.catalogs._head)
        self.assertIsNone(self.mailbox.main_peek())
        self.assertEqual(request.job.expires_us,self.now+1000)


if __name__=='__main__':unittest.main()
