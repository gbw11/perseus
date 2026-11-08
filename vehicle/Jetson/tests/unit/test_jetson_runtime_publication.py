"""원 RuntimeStatus를 실제 S-jio owner의 MQTT 입구까지 전달하는 소프트웨어 시험이다.

실제 thread와 공용 envelope codec을 사용하며 네트워크만 fake client로 대체한다.
broker 수신·기기 준비·모델 추론·이동 권한의 증명이 아니며 측정시각을 생성하지 않는다.
"""

from hashlib import sha256
import json
import threading
import time
import unittest
from unittest.mock import patch

from xenova_mqtt import envelope
from xenova_vehicle.runtime.runtime_status import RuntimeStatus
from xenova_vehicle.storage.contracts import canonical
from xenova_vehicle.transport.jetson_jio import JetsonJio, WORLD_TOPIC

import test_jetson_jio as fixtures
from test_runtime_status import status_fields


TOPIC = 'xenova/v1/mapper/oak-1/runtime'


def status(at, **changes):
    fields = status_fields()
    fields.update(vehicle='rc-test-1', device='oak-1', mapper_boot=fixtures.uid(10),
        service_instance=fixtures.uid(50), snapshot_j_us=str(at))
    fields.update(changes)
    return RuntimeStatus(fields)


class Client(fixtures.FakeClient):
    """실제 publish 호출의 순서·원 body·원 clock만 기록한다."""
    def __init__(self, clock, *, auto_done=True):
        super().__init__()
        self.clock = clock
        self.auto_done = auto_done
        self.fail_next = False
        self.records = []
        self.release_gates = []

    def publish(self, topic, raw, **kwargs):
        self.calls.append(('publish', threading.get_ident()))
        info = fixtures.Info()
        info.done = self.auto_done
        info.rc = 1 if self.fail_next else 0
        self.fail_next = False
        self.records.append(dict(topic=topic, raw=raw, options=kwargs,
            at=self.clock(), thread=threading.get_ident(), info=info))
        self.published.append((topic, raw, info))
        return info


class JetsonRuntimePublicationTests(unittest.TestCase):
    def wait_for(self, predicate):
        until = time.monotonic() + 2
        while not predicate() and time.monotonic() < until:
            time.sleep(.002)
        self.assertTrue(predicate(), 'bounded S-jio event did not occur')

    def owner(self, clock=None, *, auto_done=True):
        clock = fixtures.Clock(1_000_000) if clock is None else clock
        client = Client(clock, auto_done=auto_done)
        factory_calls = []

        def factory(*args):
            factory_calls.append(threading.get_ident())
            return client

        owner = JetsonJio(fixtures.profile(), clock=clock, client_factory=factory)

        def close():
            for gate in client.release_gates:
                gate.set()
            if client.gate is not None:
                client.gate.set()
            owner.stop()
            self.assertTrue(owner.join(1), 'S-jio owner remained alive after released fake I/O')

        self.addCleanup(close)
        return owner, client, clock, factory_calls

    def test_actual_owner_publishes_original_status_without_mapper_source_or_time_lease(self):
        owner, client, clock, factory_calls = self.owner()
        original = status(clock.now)
        self.assertTrue(owner.offer_runtime(original))
        self.assertEqual(factory_calls, [])
        self.assertEqual(client.calls, [])
        owner.start()
        self.wait_for(lambda: owner.snapshot()['counts']['runtime_published'] == 1)
        record = client.records[0]
        value = json.loads(record['raw'])
        envelope.validate(record['topic'], value)
        self.assertEqual(record['topic'], TOPIC)
        self.assertEqual(record['options'], dict(qos=0, retain=False))
        self.assertEqual((value['type'], value['source_id'], value['seq']),
            ('mapper.runtime', 'jetson', 1))
        self.assertEqual((value['boot_id'], value['agent_instance_id']),
            (original.fields['mapper_boot'], original.fields['service_instance']))
        self.assertEqual(value['payload'], original.fields)
        self.assertEqual(value['payload']['snapshot_j_us'], '1000000')
        self.assertLessEqual(len(original.canonical_bytes), 2048)
        self.assertLessEqual(len(record['raw']), 4096)
        self.assertIsNone(owner._source)
        self.assertIsNone(owner.current_lease)
        self.assertFalse(owner._thread.daemon)
        self.assertNotEqual(factory_calls[0], threading.get_ident())
        self.assertEqual({row[1] for row in client.calls}, {factory_calls[0]})

    def test_latest_burst_and_actual_acceptance_interval_do_not_retime_or_reset_sequence(self):
        owner, client, clock, _ = self.owner()
        for at in (1_000_000, 1_000_001, 1_000_002):
            clock.now = at
            self.assertTrue(owner.offer_runtime(status(at)))
        owner.start()
        self.wait_for(lambda: len(client.records) == 1)
        first = client.records[0]
        self.assertEqual(json.loads(first['raw'])['payload']['snapshot_j_us'], '1000002')
        self.assertEqual(owner.snapshot()['counts']['runtime_replaced'], 2)
        clock.now = 1_000_100
        queued = status(clock.now)
        self.assertTrue(owner.offer_runtime(queued))
        self.assertFalse(owner.offer_runtime(queued))
        clock.now = first['at'] + 999_999
        time.sleep(.02)
        self.assertEqual(len(client.records), 1)
        clock.now += 1
        self.wait_for(lambda: len(client.records) == 2)
        second = client.records[1]
        self.assertGreaterEqual(second['at'] - first['at'], 1_000_000)
        self.assertEqual(json.loads(second['raw'])['seq'], 2)
        self.assertEqual(json.loads(second['raw'])['payload'], queued.fields)
        self.assertEqual(owner._runtime_head, (1_000_100, queued.canonical_bytes))

    def test_queue_delay_drops_at_original_expiry_without_publishing_or_retiming_snapshot(self):
        owner, client, clock, _ = self.owner()
        client.gate = threading.Event()
        original = status(clock.now)
        self.assertTrue(owner.offer_runtime(original))
        owner.start()
        self.wait_for(client.entered_loop.is_set)
        clock.now += 1_000_000
        client.gate.set()
        self.wait_for(lambda: owner.snapshot()['counts']['runtime_expired'] == 1)
        self.assertEqual(client.records, [])
        self.assertFalse(owner.offer_runtime(original))
        self.assertEqual(owner._runtime_head, (1_000_000, original.canonical_bytes))
        self.assertTrue(owner.snapshot()['alive'])

    def test_source_change_fences_selected_status_and_requires_a_post_transition_snapshot(self):
        owner, client, clock, _ = self.owner()
        fixtures.bind(owner)
        entered, release = threading.Event(), threading.Event()
        client.release_gates.append(release)
        original_packet = owner._runtime_packet
        first_call = True

        def delayed_packet(item):
            nonlocal first_call
            result = original_packet(item)
            if first_call:
                first_call = False
                entered.set()
                release.wait(1)
            return result

        old = status(clock.now)
        self.assertTrue(owner.offer_runtime(old))
        with patch.object(owner, '_runtime_packet', side_effect=delayed_packet):
            owner.start()
            self.wait_for(entered.is_set)
            clock.now += 10
            changed = fixtures.source()
            changed['mapper_instance'] = fixtures.uid(90)
            self.assertTrue(fixtures.bind(owner, changed))
            self.assertFalse(owner.offer_runtime(old))
            current = status(clock.now, mapper_instance=fixtures.uid(90))
            self.assertTrue(owner.offer_runtime(current))
            release.set()
            self.wait_for(lambda: owner.snapshot()['counts']['runtime_published'] == 1)
        self.assertEqual(len(client.records), 1)
        self.assertEqual(json.loads(client.records[0]['raw'])['payload'], current.fields)
        self.assertEqual(json.loads(client.records[0]['raw'])['seq'], 1)
        self.assertEqual(owner.snapshot()['counts']['runtime_fenced'], 1)
        clock.now += 10
        owner.fence_source()
        self.assertFalse(owner.offer_runtime(current))
        self.assertTrue(owner.offer_runtime(status(clock.now, mapper_instance=None)))
        self.assertTrue(owner.snapshot()['runtime_pending'])

    def test_clock_reply_then_world_then_runtime_share_one_actual_inflight(self):
        owner, client, clock, _ = self.owner(fixtures.Clock(1_300_200), auto_done=False)
        fixtures.bind(owner)
        fixtures.qualify_clock(owner)
        self.assertTrue(owner.offer_world(fixtures.world(), expires_j_us=1_400_000,
            publication_sha256=sha256(canonical(fixtures.publication())).hexdigest()))
        self.assertTrue(owner.offer_runtime(status(clock.now)))
        owner.start()
        self.wait_for(lambda: len(client.records) == 1)
        self.assertEqual(client.records[0]['topic'], 'xenova/v1/mapper/oak-1/clock/reply.pb')
        time.sleep(.02)
        self.assertEqual(len(client.records), 1)
        client.records[0]['info'].done = True
        self.wait_for(lambda: len(client.records) == 2)
        self.assertEqual(client.records[1]['topic'], WORLD_TOPIC)
        time.sleep(.02)
        self.assertEqual(len(client.records), 2)
        client.records[1]['info'].done = True
        self.wait_for(lambda: len(client.records) == 3)
        self.assertEqual(client.records[2]['topic'], TOPIC)
        self.assertEqual(json.loads(client.records[2]['raw'])['seq'], 1)
        self.assertTrue(all(row['options'] == dict(qos=0, retain=False) for row in client.records))

    def test_uncertain_network_attempt_consumes_sequence_and_never_reissues_original_status(self):
        owner, client, clock, _ = self.owner()
        client.fail_next = True
        original = status(clock.now)
        self.assertTrue(owner.offer_runtime(original))
        owner.start()
        self.wait_for(lambda: owner.snapshot()['counts']['network_failed'] == 1)
        self.assertEqual(owner.snapshot()['counts']['runtime_published'], 0)
        self.assertFalse(owner.offer_runtime(original))
        clock.now += 1_000_000
        fresh = status(clock.now)
        self.assertTrue(owner.offer_runtime(fresh))
        self.wait_for(lambda: owner.snapshot()['counts']['runtime_published'] == 1)
        self.assertEqual(len(client.records), 2)
        values = [json.loads(row['raw']) for row in client.records]
        self.assertEqual([row['seq'] for row in values], [1, 2])
        self.assertEqual([row['payload'] for row in values], [original.fields, fresh.fields])
        self.assertGreaterEqual(client.records[1]['at'] - client.records[0]['at'], 1_000_000)
        self.assertTrue(owner.snapshot()['alive'])

    def test_exact_profile_identity_and_envelope_cap_fail_closed_without_stopping_clock_owner(self):
        owner, client, clock, factory_calls = self.owner()
        with self.assertRaises(ValueError):
            owner.offer_runtime(status(clock.now).fields)
        for changes in (dict(vehicle='foreign'), dict(device='foreign')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                owner.offer_runtime(status(clock.now, **changes))
        for key in ('mapper_boot', 'service_instance'):
            with self.subTest(missing=key):
                self.assertFalse(owner.offer_runtime(status(clock.now, **{key: None})))
        self.assertEqual(factory_calls, [])
        self.assertTrue(owner.offer_runtime(status(clock.now)))
        self.assertFalse(owner.offer_runtime(status(clock.now + 1)))
        self.assertFalse(owner.offer_runtime(status(clock.now, service_instance=fixtures.uid(51))))
        original_build = envelope.build

        def oversized(*args, **kwargs):
            value = original_build(*args, **kwargs)
            value['padding'] = 'x' * 4096
            return value

        with patch.object(envelope, 'build', side_effect=oversized):
            owner.start()
            self.wait_for(lambda: owner.snapshot()['runtime_error'] == 'ENCODE_OR_CAP_REJECTED')
        self.assertEqual(client.records, [])
        self.assertTrue(owner.snapshot()['alive'])
        self.assertIsNone(owner.snapshot()['failure'])


if __name__ == '__main__':
    unittest.main()
