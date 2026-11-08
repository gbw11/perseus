"""원 S/M seqpacket에서 출력 전 callback과 negative fence의 순서를 검사한다.

Vehicle·grant·clock은 기존 software transport fixture다. 실제 센서 admission/HIL이나
Pi 사용권 수락을 주장하지 않는다. send 역압/부분 송신은 명시 proxy 주입이며, 실제
kernel 현상 실측으로 세지 않는다. callback이 알기 전 시작한 송신의 recall도 없다.
"""

from base64 import b64decode
from hashlib import sha256
import json
import socket
import threading
import unittest

import test_jetson_jio as jio_fixture
import test_scene_source_ports as scene_fixture
import test_source_runtime as source_fixture
from xenova_drive_world import decode, encode, schema
from xenova_vehicle.runtime.data_channel import DataPacket
from xenova_vehicle.storage.contracts import canonical


def publication_sha(mapper): return sha256(canonical(mapper.publication)).hexdigest()


class SendObserver:
    """실제 endpoint의 send만 관측하거나 실패를 주입한다. 수신/원 return은 보존한다."""

    def __init__(self, endpoint, *, events=None, failure=None):
        self.original, self.events, self.failure = endpoint, events, failure

    def __getattr__(self, name): return getattr(self.original, name)

    def send(self, raw):
        if self.failure == 'BLOCK': raise BlockingIOError()
        if self.failure == 'PARTIAL': return len(raw) - 1
        if self.events is not None:
            packet = DataPacket(raw)
            self.events.append(('SEND', packet.kind, threading.get_ident()))
        return self.original.send(raw)


@unittest.skipUnless(hasattr(socket, 'SOCK_SEQPACKET'), 'actual Linux seqpacket required')
class MapperSourceAdmissionTests(unittest.TestCase):
    def runtime(self, *, bound=True):
        value = source_fixture.SourceRuntimeTests(methodName='runTest')
        value.setUp()
        self.addCleanup(value.tearDown)
        if bound:
            value.vehicle()
            value.cycle()
        return value

    def world(self, fixture, sequence=1):
        value = decode(jio_fixture.world(sequence), schema.WorldSnapshot)
        for key, item in fixture.mapper.source.items():
            if key != 'device': setattr(value, key, item)
        value.observed_us = fixture.now - 50
        value.completed_us = value.published_us = fixture.now
        return encode(value)

    def offer(self, fixture, raw, expires):
        return fixture.mapper.offer_world(raw, publication_sha256=publication_sha(fixture.mapper),
            issued_us=fixture.now, expires_us=expires)

    def send_input(self, fixture, kind, raw, *, probe=None):
        metadata = dict(received_j_us=str(fixture.now), sensor_id=None, clock_probe_sha256=probe)
        packet = fixture.supervisor.link.send(kind, metadata, raw,
            issued_us=fixture.now, expires_us=fixture.now + 100_000)
        self.assertIs(fixture.supervisor.link.flush().packet, packet)
        return packet

    def scene(self, fixture):
        helper = scene_fixture.SceneSourcePortTests(methodName='runTest')
        return helper.original_scene(fixture)

    def test_callback_brackets_initial_flush_and_all_received_inputs_before_stage_dispatch(self):
        f = self.runtime()
        mapper = f.mapper
        events = []
        mapper.link.endpoint = SendObserver(mapper.link.endpoint, events=events)
        original = mapper.link.send('WORLD_CANDIDATE', dict(publication_sha256=publication_sha(mapper)),
            self.world(f), issued_us=f.now, expires_us=f.now + 100_000)
        self.assertTrue(self.offer(f, self.world(f, 2), f.now + 100_000))
        f.now += 100
        f.sample.sequence += 1
        f.sample.source_sample += 1
        f.sample.source_us += 1
        f.sample.pi_sample_us = f.now
        vehicle = encode(f.sample)
        self.send_input(f, 'SOURCE_VEHICLE', vehicle)
        probe = schema.MapperClockProbe(major=1, vehicle=f.policy.vehicle, device=mapper.device,
            pi_host_boot=f.sample.host_boot, pi_client_instance=source_fixture.uid(), probe_seq=1,
            nonce=bytes([8] * 16), pi_send_us=f.now, admission_revision=1)
        probe_raw = encode(probe)
        probe_sha = sha256(probe_raw).hexdigest()
        self.send_input(f, 'SOURCE_CLOCK', probe_raw)
        callbacks = []
        def before_outputs():
            value = json.loads(mapper.context_bytes())
            latest = decode(b64decode(value['vehicles'][-1]['raw']), schema.VehicleSample)
            callbacks.append((latest.sequence, tuple(value['probes']), threading.get_ident()))
            events.append(('CALLBACK', latest.sequence, threading.get_ident()))
        mapper.pump(before_outputs=before_outputs)
        self.assertEqual([event[0] for event in events], ['CALLBACK', 'SEND', 'CALLBACK', 'SEND'])
        self.assertEqual([row[0] for row in callbacks], [1, 2])
        self.assertEqual(callbacks[0][1], ())
        self.assertIn(probe_sha, callbacks[1][1])
        self.assertTrue(all(row[2] == threading.get_ident() for row in events))
        sent = f.supervisor.link.poll()
        self.assertEqual(tuple(decode(packet.body, schema.WorldSnapshot).sequence for packet in sent), (1, 2))
        self.assertEqual(sent[0].raw, original.raw)
        self.assertEqual((sent[0].issued_us, sent[0].expires_us), (1000, 101000))
        # 두 번째 입력의 None lease를 기존/새 trust로 채우지 않는다.
        self.assertIsNone(mapper.active_probe)
        self.assertIsNone(json.loads(mapper.context_bytes())['vehicles'][-1]['clock_probe_sha256'])

    def test_dynamic_clock_expiry_callback_discards_exact_pending_before_flush_without_renewing_e(self):
        f = self.runtime()
        grant, lease = self.scene(f)
        mapper = f.mapper
        raw = self.world(f)
        expires = f.now + 2_000_000
        self.assertTrue(self.offer(f, raw, expires))
        proxy = SendObserver(mapper.link.endpoint, failure='BLOCK')
        mapper.link.endpoint = proxy
        mapper.pump()
        original = mapper.link.pending
        publication, source, active_probe = mapper.publication, mapper.source, mapper.active_probe
        self.assertIsNotNone(original)
        proxy.failure = None
        f.now = lease.expires_pi_us
        callbacks = []
        def before_outputs():
            callbacks.append(threading.get_ident())
            try:
                lease.pi_interval_to_j((lease.probe_pi_send_us, lease.probe_pi_send_us), f.now)
            except ValueError:
                mapper.discard_outputs()
        mapper.pump(before_outputs=before_outputs)
        self.assertEqual(len(callbacks), 2)
        self.assertIsNone(mapper.link.pending)
        self.assertEqual(f.supervisor.link.poll(), ())
        self.assertEqual(mapper.counts['output_fenced'], 1)
        self.assertEqual((original.issued_us, original.expires_us), (1000, expires))
        self.assertIs(mapper.publication, publication)
        self.assertIs(mapper.source, source)
        self.assertEqual(mapper.active_probe, active_probe)
        self.assertFalse(mapper.offer_world(raw, publication_sha256=publication_sha(mapper),
            issued_us=original.issued_us, expires_us=original.expires_us))
        with self.assertRaisesRegex(ValueError, 'renewed'):
            mapper.offer_world(raw, publication_sha256=publication_sha(mapper),
                issued_us=original.issued_us, expires_us=original.expires_us + 1)

    def test_new_negative_scene_fences_stage_world_and_summary_before_publication_changes(self):
        f = self.runtime()
        grant, lease = self.scene(f)
        mapper = f.mapper
        publication, source, context = mapper.publication, mapper.source, mapper.storage_context
        old_probe = mapper.active_probe
        raw = self.world(f)
        expires = f.now + 100_000
        self.assertTrue(self.offer(f, raw, expires))
        summary = encode(scene_fixture.summary(grant, f.now))
        self.assertTrue(mapper.offer_survey_summary(summary, publication_sha256=publication_sha(mapper),
            grant_sha256=sha256(encode(grant)).hexdigest(), issued_us=f.now, expires_us=expires))
        negative = schema.SceneGrant()
        negative.CopyFrom(grant)
        negative.lifecycle_state = 3
        negative.lifecycle_revision += 1
        self.send_input(f, 'SOURCE_SCENE', encode(negative))
        observed = []
        def before_outputs():
            value = decode(b64decode(mapper.scene['raw']), schema.SceneGrant)
            observed.append(value.lifecycle_state)
            if value.lifecycle_state in (3, 4): mapper.discard_outputs()
        mapper.pump(before_outputs=before_outputs)
        self.assertEqual(observed, [2, 3])
        self.assertEqual(f.supervisor.link.poll(), ())
        self.assertEqual(mapper.counts['output_fenced'], 2)
        self.assertIs(mapper.publication, publication)
        self.assertIs(mapper.source, source)
        self.assertIs(mapper.storage_context, context)
        self.assertEqual(mapper.active_probe, old_probe)
        self.assertEqual(decode(b64decode(mapper.scene['raw']), schema.SceneGrant), negative)
        self.assertFalse(self.offer(f, raw, expires))
        with self.assertRaisesRegex(ValueError, 'renewed'):
            self.offer(f, raw, expires + 1)

    def test_post_poll_callback_precedes_initial_identity_and_exceptions_reentry_or_foreign_thread_do_not_send(self):
        f = self.runtime(bound=False)
        f.vehicle()
        for _ in range(2):
            f.supervisor.pump(f.material, bind_ready=True, on_publication=lambda *_: None)
        states = []
        class RejectedAdmission(Exception): pass
        def reject_new_input():
            states.append((f.mapper.material is not None, len(f.mapper.vehicles), f.mapper._proposal_packet))
            if f.mapper.material is not None: raise RejectedAdmission()
        with self.assertRaises(RejectedAdmission):
            f.mapper.pump(before_outputs=reject_new_input)
        self.assertEqual([(material, count) for material, count, _ in states], [(False, 0), (True, 1)])
        self.assertTrue(all(original is None for _, _, original in states))
        self.assertEqual(f.supervisor.link.poll(), ())
        with self.assertRaisesRegex(RuntimeError, 'reentered'):
            f.mapper.pump(before_outputs=lambda: f.mapper.pump())
        self.assertEqual(f.supervisor.link.poll(), ())
        callbacks = []
        failures = []
        def other_owner():
            try:
                f.mapper.pump(before_outputs=lambda: callbacks.append('FOREIGN'))
            except BaseException as error:
                failures.append(error)
        thread = threading.Thread(target=other_owner, name='foreign-source-owner', daemon=False)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], RuntimeError)
        self.assertEqual(callbacks, [])
        # 예외 뒤에도 원 owner는 다시 사용할 수 있다. 이전 identity를 새 시각으로 재발급한 것이 아니다.
        f.mapper.pump(before_outputs=lambda: callbacks.append('OWNER'))
        self.assertEqual(callbacks, ['OWNER', 'OWNER'])
        proposal, = f.supervisor.link.poll()
        self.assertEqual(proposal.kind, 'SOURCE_IDENTITY')
        self.assertEqual(proposal.header['metadata']['vehicle_sample_sha256'], sha256(encode(f.sample)).hexdigest())
        self.assertEqual((proposal.issued_us, proposal.expires_us), (1000, 5001000))

    def test_discard_preserves_identity_bootstrap_and_refuses_partial_or_unknown_output(self):
        for kind in ('SOURCE_IDENTITY', 'SOURCE_BOOTSTRAP_LOAD'):
            with self.subTest(kind=kind):
                f = self.runtime()
                mapper = f.mapper
                self.assertTrue(self.offer(f, self.world(f), f.now + 100))
                # 이 구조 packet은 의미 consumer에 보내지 않는다. DB/identity 승인 양성이 아니다.
                original = mapper.link.send(kind, {}, b'' if kind == 'SOURCE_IDENTITY' else b'{}',
                    issued_us=f.now, expires_us=f.now + 100)
                self.assertEqual(mapper.discard_outputs(), 1)
                self.assertIs(mapper.link.pending, original)
                self.assertEqual((original.issued_us, original.expires_us), (1000, 1100))
        f = self.runtime()
        mapper = f.mapper
        original = mapper.link.send('WORLD_CANDIDATE', dict(publication_sha256=publication_sha(mapper)),
            self.world(f), issued_us=f.now, expires_us=f.now + 100)
        mapper.link.endpoint = SendObserver(mapper.link.endpoint, failure='PARTIAL')
        with self.assertRaises(OSError): mapper.link.flush()
        with self.assertRaisesRegex(RuntimeError, 'unknown'): mapper.discard_outputs()
        self.assertIs(mapper.link.pending, original)
        self.assertEqual(mapper.counts['output_fenced'], 0)


if __name__ == '__main__':
    unittest.main()
