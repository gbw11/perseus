"""실행 근거 기록까지 완료해야 상위 생성자가 ACK를 사용할 수 있는 경계를 검사한다."""

import threading
from types import SimpleNamespace
import unittest

from resource_guard_fixtures import encode, identifier, manifest
import test_async_resource_log as fixtures
from xenova_vehicle.runtime.resource_manifest import ResourceManifest


class PublicationBoundaryTests(unittest.TestCase):
    setUp = fixtures.AsyncResourceLogTests.setUp
    actor = fixtures.AsyncResourceLogTests.actor
    result = fixtures.AsyncResourceLogTests.result
    submit = fixtures.AsyncResourceLogTests.submit

    def test_actual_bootstrap_request_and_postload_are_on_the_store_owner_thread(self):
        captured = []

        def bootstrap(store, batch, request):
            captured.append((request, threading.current_thread()))
            proposal = store.propose(batch.latest_load, manifest(), request.request_id)
            return SimpleNamespace(proposal=proposal)

        def published(store, ack, receipt):
            self.assertIs(threading.current_thread(), store.owner)
            self.assertIs(receipt, store.loads[-1])
            self.assertEqual(receipt.outcome.manifest.ref, ack.target_ref)
            captured.append((ack, receipt))

        actor = self.actor(bootstrap=bootstrap, bootstrap_with_request=True, publication=published)
        completed = self.submit(actor, 'BOOTSTRAP', number=3001)
        self.assertIsNone(completed.error)
        request, owner = captured[0]
        self.assertEqual((request.request_id, request.issued_us, request.expires_us),
                         (completed.request_id, completed.issued_us, completed.expires_us))
        self.assertIs(owner, fixtures.FakeStore.instances[0].owner)
        self.assertIs(captured[1][0], completed.ack)
        self.assertIs(captured[1][1], completed.receipt)

    def test_execution_record_failure_keeps_original_pending_and_blocks_another_write(self):
        def unavailable(store, ack, receipt):
            raise OSError('injected execution record fsync failure')

        actor = self.actor(publication=unavailable)
        old = manifest()
        fields = old.fields
        fields['revision'] = '2'
        target = ResourceManifest(encode(fields))
        first = self.submit(actor, 'WRITE', number=3002, manifest=target, expected_ref=old.ref)
        self.assertIn('execution record fsync failure', first.error)
        self.assertIsNotNone(first.ack)
        self.assertFalse(first.timely(first.completed_us))
        fields['revision'] = '3'
        second = self.submit(actor, 'WRITE', number=3003,
            manifest=ResourceManifest(encode(fields)), expected_ref=target.ref)
        self.assertIn('identical original request', second.error)
        self.assertEqual(len(fixtures.FakeStore.instances[0].published), 1)


if __name__ == '__main__':
    unittest.main()
