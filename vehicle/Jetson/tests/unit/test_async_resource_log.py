"""S-log 원 Thread·실제 receipt 연결의 작은 fake-store 시험. 내구 I/O 증거는 아니다."""

import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from resource_guard_fixtures import encode, identifier, manifest
from raw_lease_fixtures import scope
from xenova_vehicle.runtime import async_resource_log as log
from xenova_vehicle.runtime.resource_guard import LoadOutcome
from xenova_vehicle.runtime.resource_manifest import ResourceManifest
from xenova_vehicle.runtime.resource_store import StoreAck, StoreLoad, StoreProposal, StoreResult
from xenova_vehicle.runtime.raw_leases import RawOwnerExitBarrier, RawOwnerExitEvidence


class FakeStore:
    instances = []
    unconfirmed = False

    def __init__(self, directory):
        self.owner = threading.current_thread()
        self.current = manifest()
        self.loads = []
        self.proposals = []
        self.published = []
        self.reconciled = []
        self.closed_by = None
        self.__class__.instances.append(self)

    def load(self):
        assert threading.current_thread() is self.owner
        receipt = StoreLoad(identifier(900), LoadOutcome(identifier(1000 + len(self.loads)), 'VALID', self.current))
        self.loads.append(receipt)
        return receipt

    def propose(self, receipt, target, request_id):
        assert threading.current_thread() is self.owner and receipt is self.loads[-1]
        canonical = ResourceManifest(target.canonical_bytes)
        proposal = StoreProposal(request_id, receipt.lock_nonce, receipt, target,
                                 target.raw_sha256, canonical.raw_bytes, canonical.ref)
        self.proposals.append(proposal)
        return proposal

    def publish(self, proposal):
        assert threading.current_thread() is self.owner
        self.published.append(proposal)
        if self.unconfirmed:
            return StoreResult('UNCONFIRMED', reason='injected')
        self.current = ResourceManifest(proposal.target_bytes)
        return StoreResult('DURABLE', StoreAck(proposal, 'REPLACED'))

    def reconcile(self, proposal):
        self.reconciled.append(proposal)
        self.current = ResourceManifest(proposal.target_bytes)
        return StoreResult('DURABLE', StoreAck(proposal, 'RECONFIRMED'))

    def close(self):
        self.closed_by = threading.current_thread()


class FakeObserver:
    def __init__(self, store, service):
        self.store = store

    def observe(self):
        return SimpleNamespace(original_load=self.store.load(), latest_load=self.store.load())


class AsyncResourceLogTests(unittest.TestCase):
    def setUp(self):
        FakeStore.instances = []
        FakeStore.unconfirmed = False
        self.patches = [patch.object(log, 'ResourceStore', FakeStore),
                        patch.object(log, 'ProcessObserver', FakeObserver)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.actors = []

    def actor(self, **kwargs):
        actor = log.AsyncResourceLog('/synthetic-state', identifier(2), **kwargs).start()
        self.actors.append(actor)
        self.addCleanup(actor.close)
        return actor

    def result(self, actor):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = actor.poll()
            if result is not None:
                return result
            threading.Event().wait(0.001)
        self.fail('S-log completion not delivered')

    def submit(self, actor, operation, number=2000, **kwargs):
        now = time.monotonic_ns() // 1000
        self.assertTrue(actor.submit(identifier(number), operation, issued_us=now,
                                     expires_us=now + 5000000, **kwargs))
        return self.result(actor)

    def test_prepare_and_store_close_stay_on_original_log_thread(self):
        main = threading.current_thread()
        actor = self.actor(prepare=threading.current_thread)
        completed = self.submit(actor, 'PREPARE')
        self.assertIsNone(completed.error)
        self.assertIs(completed.value, FakeStore.instances[0].owner)
        self.assertIsNot(completed.value, main)
        actor.close()
        self.assertIs(FakeStore.instances[0].closed_by, completed.value)

    def test_write_preserves_original_proposal_and_actual_post_receipt(self):
        actor = self.actor()
        old = manifest()
        fields = old.fields
        fields['revision'] = '2'
        target = ResourceManifest(encode(fields))
        completed = self.submit(actor, 'WRITE', manifest=target, expected_ref=old.ref)
        store = FakeStore.instances[0]
        self.assertIsNone(completed.error)
        self.assertIs(completed.ack.proposal, store.proposals[0])
        self.assertIs(completed.receipt, store.loads[-1])
        self.assertEqual(completed.receipt.outcome.manifest.ref, completed.ack.target_ref)
        self.assertTrue(completed.timely(time.monotonic_ns() // 1000))

    def test_original_ref_mismatch_never_proposes(self):
        actor = self.actor()
        completed = self.submit(actor, 'WRITE', manifest=manifest(), expected_ref=None)
        self.assertIsNotNone(completed.error)
        self.assertEqual(FakeStore.instances[0].proposals, [])
        self.assertIsNone(completed.ack)

    def test_bootstrap_publishes_exact_registered_proposal_without_fresh_load(self):
        made = []
        request_id = identifier(2001)
        def bootstrap(store, batch):
            target = manifest()
            proposal = store.propose(batch.latest_load, target, request_id)
            value = SimpleNamespace(proposal=proposal, manifest=target)
            made.append(value)
            return value
        actor = self.actor(bootstrap=bootstrap)
        completed = self.submit(actor, 'BOOTSTRAP', number=2001)
        store = FakeStore.instances[0]
        self.assertIsNone(completed.error)
        self.assertIs(completed.value, made[0])
        self.assertIs(store.published[0], made[0].proposal)
        self.assertEqual(len(store.loads), 3)
        self.assertIs(completed.receipt, store.loads[-1])

    def test_completion_keeps_historical_time_and_late_ack(self):
        actor = self.actor()
        old = manifest()
        fields = old.fields
        fields['revision'] = '2'
        target = ResourceManifest(encode(fields))
        with patch.object(log, '_now', side_effect=[110, 110, 200]):
            self.assertTrue(actor.submit(identifier(2100), 'WRITE', issued_us=100, expires_us=150,
                                         manifest=target, expected_ref=old.ref))
            completed = self.result(actor)
        self.assertIsNotNone(completed.ack)
        self.assertTrue(completed.late)
        self.assertFalse(completed.timely(200))
        self.assertEqual(completed.completed_us, 200)

    def test_unconfirmed_write_reconciles_same_original_request(self):
        FakeStore.unconfirmed = True
        actor = self.actor()
        old = manifest()
        fields = old.fields
        fields['revision'] = '2'
        target = ResourceManifest(encode(fields))
        now = time.monotonic_ns() // 1000
        args = dict(issued_us=now, expires_us=now + 5000000, manifest=target, expected_ref=old.ref)
        self.assertTrue(actor.submit(identifier(2200), 'WRITE', **args))
        first = self.result(actor)
        self.assertEqual(first.value.kind, 'UNCONFIRMED')
        self.assertTrue(actor.submit(identifier(2200), 'WRITE', **args))
        second = self.result(actor)
        store = FakeStore.instances[0]
        self.assertEqual(second.value.kind, 'DURABLE')
        self.assertEqual(len(store.proposals), 1)
        self.assertIs(store.reconciled[0], store.proposals[0])

    def test_pending_completion_applies_backpressure(self):
        actor = self.actor(prepare=lambda: 'prepared')
        now = time.monotonic_ns() // 1000
        self.assertTrue(actor.submit(identifier(2300), 'PREPARE', issued_us=now, expires_us=now + 5000000))
        self.assertFalse(actor.submit(identifier(2301), 'OBSERVE', issued_us=now, expires_us=now + 5000000))
        self.assertEqual(self.result(actor).value, 'prepared')

    def test_completion_deadline_and_builtin_guards(self):
        completed = log.ResourceLogCompletion(identifier(2400), 'OBSERVE', 100, 150, 120)
        self.assertTrue(completed.timely(120))
        self.assertFalse(completed.timely(150))
        with self.assertRaises(ValueError):
            completed.timely(True)
        with self.assertRaises(ValueError):
            log.ResourceLogRequest(identifier(2401), 'WRITE', 100, 150)

    def current_ack(self):
        m = manifest()
        original = StoreLoad(identifier(2500), LoadOutcome(identifier(2501), 'VALID', m))
        target = ResourceManifest(m.canonical_bytes)
        proposal = StoreProposal(identifier(2502), original.lock_nonce, original, m,
                                 m.raw_sha256, target.raw_bytes, target.ref)
        return StoreAck(proposal, 'REPLACED')

    def test_recovery_native_commands_use_original_ack_and_operation_uuid(self):
        calls = []
        class Native:
            def normalize_raw(self, original_ack, operation_id, barrier):
                calls.append(('normalize', original_ack, operation_id, barrier))
                return RawOwnerExitEvidence(barrier, operation_id, time.monotonic_ns() // 1000)

            def retire_raw(self, original_ack, operation_id):
                calls.append(('retire', original_ack, operation_id))
                return 'retired-fixture'

        actor = self.actor(native=Native())
        original_ack = self.current_ack()
        barrier = RawOwnerExitBarrier(scope(), 100, 200)
        result = self.submit(actor, 'NORMALIZE_RAW', number=2510,
                             intent_ack=original_ack, recovery_barrier=barrier)
        self.assertIsNone(result.error)
        self.assertIs(result.value.barrier, barrier)
        self.assertEqual(calls[0], ('normalize', original_ack, identifier(2510), barrier))
        retired = self.submit(actor, 'RETIRE_RAW', number=2511, intent_ack=original_ack)
        self.assertEqual(retired.value, 'retired-fixture')
        self.assertEqual(calls[1], ('retire', original_ack, identifier(2511)))

    def test_unconfirmed_publication_blocks_recovery_but_cleanup_keeps_original_pending(self):
        calls = []
        class Native:
            def normalize_raw(self, *args):
                calls.append('normalize')

            def close_after_children(self):
                calls.append('cleanup')

        FakeStore.unconfirmed = True
        actor = self.actor(native=Native())
        old = manifest()
        fields = old.fields
        fields['revision'] = '2'
        target = ResourceManifest(encode(fields))
        now = time.monotonic_ns() // 1000
        original_args = dict(issued_us=now, expires_us=now + 5000000,
                              manifest=target, expected_ref=old.ref)
        self.assertTrue(actor.submit(identifier(2520), 'WRITE', **original_args))
        self.assertEqual(self.result(actor).value.kind, 'UNCONFIRMED')
        blocked = self.submit(actor, 'NORMALIZE_RAW', number=2521, intent_ack=self.current_ack(),
                              recovery_barrier=RawOwnerExitBarrier(scope(), 100, 200))
        self.assertIsNotNone(blocked.error)
        cleaned = self.submit(actor, 'CLEANUP', number=2522)
        self.assertIsNone(cleaned.error)
        self.assertEqual(calls, ['cleanup'])
        self.assertTrue(actor.submit(identifier(2520), 'WRITE', **original_args))
        self.assertEqual(self.result(actor).value.kind, 'DURABLE')
        self.assertEqual(len(FakeStore.instances[0].proposals), 1)

    def test_recovery_barrier_is_exact_and_only_normalize_accepts_it(self):
        original_ack = self.current_ack()
        barrier = RawOwnerExitBarrier(scope(), 100, 200)
        request = log.ResourceLogRequest(identifier(2530), 'NORMALIZE_RAW', 100, 300,
                                         intent_ack=original_ack, recovery_barrier=barrier)
        self.assertIs(request.recovery_barrier, barrier)
        with self.assertRaises(ValueError):
            log.ResourceLogRequest(identifier(2531), 'NORMALIZE_RAW', 100, 300, intent_ack=original_ack)
        with self.assertRaises(ValueError):
            log.ResourceLogRequest(identifier(2532), 'RETIRE_RAW', 100, 300,
                                    intent_ack=original_ack, recovery_barrier=barrier)


if __name__ == '__main__':
    unittest.main()
