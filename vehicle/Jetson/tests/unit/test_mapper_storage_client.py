"""M storage pump의 실제-send/compute 분리와 원 lease 사건 순서를 검사한다.

fake actor/arena의 완료는 실제 IPC/반납/SQL 증명이 아니다. 순수 copy codec의 원 bytes와
기한을 쓰며 native 통합은 root의 Nano 흐름에서 별도로 확인한다.
"""

from dataclasses import replace
from hashlib import sha256
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from xenova_vehicle.runtime.lifecycle_messages import LifecyclePeer
from xenova_vehicle.runtime.local_envelope import encode_structure
from xenova_vehicle.runtime.mapper_storage import MapperStorageMailbox
from xenova_vehicle.runtime.mapper_storage_client import MapperStorageClient, MapperStorageClientError
from xenova_vehicle.runtime.native_resources import ArenaSpec, DB_COPY_BYTES
from xenova_vehicle.runtime.raw_leases import Endpoint
from xenova_vehicle.runtime.resident_db import DbAbort
from xenova_vehicle.runtime.resident_transport import SentPacket
from xenova_vehicle.runtime.slot_cleanup import CleanupTimeLimits
from xenova_vehicle.runtime.storage_copy import (CopyLease, CopyReturned, OwnedCopy, encode_load)
from xenova_vehicle.runtime.storage_messages import db_payload
from xenova_vehicle.storage.contracts import (StorageContext, StorageJob, LoadRequest, CommitReceipt,
    CatalogHeadSnapshot, canonical)
from xenova_vehicle.storage.port import CheckpointRequest
from xenova_vehicle.storage.records import CheckpointPayload, LoadedCheckpoint


def uid(n): return '30000000-0000-4000-8000-%012x' % n


class Clock:
    now = 110
    def __call__(self): return self.now


class Link:
    def __init__(self, peer, clock):
        self.peer = peer; self.clock = clock; self.blocked = False
        self.pending = None; self.sent = []; self.sequence = 0; self.packets = []
    def send(self, kind, payload, **kw):
        self.flush()
        if self.pending is not None: return None
        self.sequence += 1
        packet = encode_structure(dict(version=1, service_instance=self.peer.service_instance,
            channel_epoch=self.peer.channel_epoch, sender_instance=self.peer.worker_instance,
            message_sequence=str(self.sequence), kind=kind, binding_domain=kw['domain'],
            binding_revision=str(kw['revision']), request_id=kw['request_id'], issued_j_us=str(kw['issued_us']),
            expires_j_us=str(kw['expires_us']), payload=payload))
        self.pending = packet; self.packets.append(packet); self.flush(); return packet
    def flush(self):
        if self.pending is not None and not self.blocked:
            self.sent.append(SentPacket(self.pending, self.clock(), self.clock())); self.pending = None


class Actor:
    def __init__(self, peer, clock):
        self.boot = SimpleNamespace(peer=peer, cleanup_limits=CleanupTimeLimits(100))
        self.link = Link(peer, clock); self.last_actual_sent = None; self.client = None
    def drain_sent(self):
        sent, self.link.sent = self.link.sent, []
        for value in sent:
            self.last_actual_sent = value
            if self.client is not None: self.client.note_sent(value)


class Arena:
    def __init__(self):
        self.held = False; self.publishes = self.reads = self.posts = 0
        self.owned = None; self.clock = None; self.fail_publish = False
    def publish(self, lease, encoding, *, expires_us):
        self.publishes += 1
        if self.fail_publish:
            self.held = True
            raise RuntimeError('controlled ownership uncertainty')
        return encoding.ready
    def copy_raw_owned(self, lease, ready, *, expires_us):
        self.reads += 1; self.posts += 1
        if self.owned is None: raise RuntimeError('missing synthetic owned copy')
        return self.owned, CopyReturned(lease, self.clock(), self.clock())
    def close(self):
        if self.held: raise RuntimeError('held owner cannot close')


class MapperStorageClientTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(); self.peer = LifecyclePeer(uid(1), uid(2), uid(3), 'M', 'c' * 64)
        self.binding = dict(service_instance=uid(1), mapper_worker=uid(3), mapper_instance=uid(4),
            raw_binding_sha256='a' * 64, map_epoch=uid(10), frame_epoch=uid(11), odom_epoch=uid(12),
            configuration='test-config', calibration='c' * 64, primitive='d' * 64,
            scene_admission_ref=None, source_binding_revision='1')
        self.context = StorageContext(uid(5), uid(3), uid(4), uid(10), uid(11), uid(12), uid(13),
            'test-config', 'c' * 64, 'd' * 64, canonical(self.binding))
        job = StorageJob(self.context, uid(20), 100, 1000)
        self.request = CheckpointRequest(job, CheckpointPayload.build(job, durable_cutoff_pi_us=1))
        self.mailbox = MapperStorageMailbox(self.context.sha256)
        self.actor = Actor(self.peer, self.clock); self.arena = Arena(); self.arena.clock = self.clock
        self.spec = ArenaSpec(uid(6), '/xnv_db_' + uid(6), DB_COPY_BYTES, 'DB_COPY')
        self.client = MapperStorageClient(self.actor, self.mailbox, (self.spec, ('fake-writable', 'fake-ready')),
            clock_us=self.clock, arena_factory=lambda *a, **kw: self.arena)
        self.actor.client = self.client; self.s_sequence = 0

    def pump(self): self.client.pump(current_context_sha256=self.context.sha256, now_us=self.clock())

    def s_packet(self, kind, payload, *, issued=None, domain='STORAGE', request=None):
        self.s_sequence += 1
        job = (self.request if request is None else request).job
        return encode_structure(dict(version=1, service_instance=self.peer.service_instance,
            channel_epoch=self.peer.channel_epoch, sender_instance=self.peer.service_instance,
            message_sequence=str(self.s_sequence), kind=kind, binding_domain=domain, binding_revision='1',
            request_id=job.job_id, issued_j_us=str(self.clock() if issued is None else issued),
            expires_j_us=str(job.expires_us), payload=payload))

    def writer(self):
        return CopyLease(uid(1), self.spec.arena_id, 1, Endpoint(uid(2), uid(3)), Endpoint(uid(30), uid(31)), uid(32),
            raw_binding_sha256='a' * 64, publication_binding_sha256=sha256(self.context.source_binding).hexdigest())

    def start_checkpoint(self):
        self.mailbox.compute_offer(self.request, now_us=self.clock()); self.pump()
        self.assertEqual(self.client.state, 'WAIT_WRITER')
        self.clock.now = 130
        self.client.receive(self.s_packet('SLOT', self.writer().slot_payload('GRANT')))

    def make_load(self, *, bootstrap=False):
        cp = self.request
        ref = dict(checkpoint_job_id=cp.job.job_id, durable_revision='1', manifest_sha256=sha256(cp.payload.manifest).hexdigest(),
            manifest_length=len(cp.payload.manifest), tile_count=0, map_epoch=uid(10), frame_epoch=uid(11),
            odom_epoch=uid(12), pi_host_boot=uid(13), configuration='test-config', calibration='c' * 64,
            primitive_sha256='d' * 64, origin_mapper_instance=uid(4))
        job = StorageJob(self.context, uid(21), 100, 1000)
        request = LoadRequest(job, canonical(ref), canonical(dict(kind='CHECKPOINT_PAGE', cursor=0)),
            'BOOTSTRAP_RESTORE' if bootstrap else 'CURRENT_MAP')
        heads = CatalogHeadSnapshot(job.job_id, self.context.sha256, uid(5), uid(10),
            (('LANE', 1, 0), ('SLOT', 1, 0)), 150, 1000)
        loaded = LoadedCheckpoint(request, cp.job, request.checkpoint_ref, cp.payload, heads, 150)
        lease = replace(self.writer(), producer=Endpoint(uid(30), uid(31)), consumer=Endpoint(uid(2), uid(3)),
            raw_binding_sha256=None if bootstrap else 'a' * 64,
            publication_binding_sha256=None if bootstrap else sha256(self.context.source_binding).hexdigest())
        encoded = encode_load(loaded, lease); read = replace(lease, read_token=uid(33))
        ready = dict(encoded.ready, copy_lease=read.fields)
        self.arena.owned = OwnedCopy(read, canonical(ready), encoded.evidence, encoded.descriptors, encoded.manifest, encoded.tiles)
        return request, ready

    def test_checkpoint_waits_for_actual_grant_and_compute_encode_before_physical_publish(self):
        from xenova_vehicle.runtime import storage_copy
        self.mailbox.compute_offer(self.request, now_us=110)
        with patch.object(storage_copy, 'encode_checkpoint', side_effect=AssertionError('main encoded')):
            self.pump(); self.clock.now = 130
            self.client.receive(self.s_packet('SLOT', self.writer().slot_payload('GRANT'))); self.pump()
        self.assertEqual(self.arena.publishes, 0)
        actual = storage_copy.encode_checkpoint
        def encode_on_compute(*args):
            self.assertFalse(self.mailbox._lock.locked()); return actual(*args)
        with patch.object(storage_copy, 'encode_checkpoint', side_effect=encode_on_compute) as encoder:
            self.mailbox.compute_encode_pending(current_context_sha256=self.context.sha256, now_us=130, clock_us=lambda:140)
            self.assertIsNone(self.mailbox.compute_encode_pending(current_context_sha256=self.context.sha256,
                now_us=140, clock_us=lambda:140))
            self.assertEqual(encoder.call_count, 1)
        self.clock.now = 150
        with patch.object(storage_copy, 'sha256', side_effect=AssertionError('main hashed encoding')):
            self.pump()
        self.assertEqual(self.arena.publishes, 1); self.assertEqual(self.client.state, 'WAIT_RESULT')
        self.assertEqual(self.actor.link.packets[-1].payload['op'], 'READY')
        self.clock.now = 180
        receipt = CommitReceipt(uid(5), uid(20), self.request.digest, 'COMMITTED', 1, 160, self.context.sha256)
        self.client.receive(self.s_packet('DB_JOB', db_payload('RESULT', receipt.fields)))
        self.assertTrue(self.mailbox.compute_take(current_context_sha256=self.context.sha256, now_us=190).current)
        self.assertFalse(any(p.payload.get('op') == 'COMMIT' for p in self.actor.link.packets))

    def test_queued_prepare_is_not_actual_sent_and_busy_keeps_original_packet(self):
        self.mailbox.compute_offer(self.request, now_us=110)
        self.actor.link.blocked = True; self.pump()
        packet = self.client._packet
        self.assertEqual(self.client.state, 'SEND_INITIAL')
        self.clock.now = 130
        with self.assertRaises(ValueError):
            self.client.receive(self.s_packet('SLOT', self.writer().slot_payload('GRANT')))
        self.assertIsNone(self.mailbox._encode_request)
        self.assertEqual(self.arena.publishes, 0)
        self.clock.now = 140; self.actor.link.blocked = False; self.pump()
        self.assertEqual(self.client.state, 'WAIT_WRITER')
        self.assertEqual(len(self.actor.link.packets), 1)
        self.assertIs(self.actor.link.packets[0], packet)
        self.assertEqual((packet.fields['issued_j_us'], packet.fields['expires_j_us']), ('100', '1000'))

    def test_load_keeps_owned_copy_out_of_compute_until_release_is_actually_sent(self):
        request, ready = self.make_load(); self.mailbox.compute_offer(request, now_us=110); self.pump()
        self.clock.now = 200
        with patch.object(OwnedCopy, 'decode', side_effect=AssertionError('main decoded')):
            self.client.receive(self.s_packet('DB_JOB', db_payload('READY', ready), request=request))
            self.actor.link.blocked = True; self.pump()
            self.assertEqual(self.client.state, 'SEND_RELEASE')
            self.assertIsNone(self.mailbox.compute_take(current_context_sha256=self.context.sha256, now_us=205))
            self.clock.now = 210; self.actor.link.blocked = False; self.pump()
        self.assertEqual((self.arena.reads, self.arena.posts), (1, 1))
        delivered = self.mailbox.compute_take(current_context_sha256=self.context.sha256, now_us=215)
        self.assertTrue(delivered.current); self.assertIs(delivered.result, self.arena.owned)
        self.assertIs(delivered.admitted_result.request, request)

    def test_expired_encode_does_not_publish_or_make_a_native_post(self):
        self.start_checkpoint()
        self.mailbox.compute_encode_pending(current_context_sha256=self.context.sha256, now_us=130, clock_us=lambda:1000)
        self.clock.now = 1000
        with self.assertRaises(MapperStorageClientError): self.pump()
        self.assertEqual((self.arena.publishes, self.arena.posts), (0, 0))
        self.assertIsNotNone(self.mailbox.main_peek()); self.assertTrue(self.client.snapshot()['ownership_pinned'])

    def test_ambiguous_native_writer_stays_pinned_until_actual_owner_recovery(self):
        self.start_checkpoint()
        self.mailbox.compute_encode_pending(current_context_sha256=self.context.sha256, now_us=130, clock_us=lambda:140)
        self.arena.fail_publish = True; self.clock.now = 150
        with self.assertRaises(MapperStorageClientError): self.pump()
        self.clock.now = 2000
        with self.assertRaises(MapperStorageClientError): self.pump()
        self.assertTrue(self.arena.held); self.assertEqual(self.arena.posts, 0)
        self.assertIsNone(self.mailbox.compute_take(current_context_sha256=self.context.sha256, now_us=2000))
        with self.assertRaises(RuntimeError): self.client.close()

    def test_storage_domain_routing_and_same_grant_do_not_repeat_encoding(self):
        self.start_checkpoint()
        raw = self.s_packet('SLOT', self.writer().slot_payload('GRANT'), domain='RAW')
        self.assertFalse(self.client.receive(raw))
        current = self.mailbox._encode_request
        self.assertTrue(self.client.receive(self.s_packet('SLOT', self.writer().slot_payload('GRANT'))))
        self.assertIs(self.mailbox._encode_request, current)
        wrong = replace(self.writer(), write_token=uid(99))
        with self.assertRaises(ValueError): self.client.receive(self.s_packet('SLOT', wrong.slot_payload('GRANT')))
        self.assertEqual(self.arena.publishes, 0)

    def test_bootstrap_adopts_s_original_load_without_duplicate_prepare_or_identity_success(self):
        request, ready = self.make_load(bootstrap=True); self.mailbox.compute_offer(request, now_us=110)
        self.assertTrue(self.client.adopt_bootstrap_load(current_context_sha256=self.context.sha256, now_us=110))
        self.assertEqual(self.actor.link.packets, [])
        self.clock.now = 200
        self.client.receive(self.s_packet('DB_JOB', db_payload('READY', ready), request=request)); self.pump()
        self.assertEqual(self.client.state, 'DELIVERED')
        self.assertFalse(hasattr(self.client, 'identity_verified'))
        result = self.mailbox.compute_take(current_context_sha256=self.context.sha256, now_us=210)
        self.assertTrue(result.current); self.assertEqual(result.admitted_result.request.purpose, 'BOOTSTRAP_RESTORE')
        self.assertTrue(all(p.kind == 'SLOT' and p.payload['op'] == 'RELEASE' for p in self.actor.link.packets))

    def test_actual_send_backpressure_keeps_original_bytes_and_late_cleanup_has_no_new_job_budget(self):
        request, ready = self.make_load(); self.mailbox.compute_offer(request, now_us=110); self.pump()
        self.clock.now = 990
        self.client.receive(self.s_packet('DB_JOB', db_payload('READY', ready), request=request))
        self.actor.link.blocked = True; self.pump(); packet = self.client._packet
        self.clock.now = 1010; self.actor.link.blocked = False; self.pump()
        self.assertEqual(packet.fields['expires_j_us'], '1000')
        delivery = self.mailbox.compute_take(current_context_sha256=self.context.sha256, now_us=1020)
        self.assertFalse(delivery.current); self.assertEqual(delivery.request.job.expires_us, 1000)
        self.assertEqual(self.arena.posts, 1)

    def test_original_db_abort_closes_only_safe_wait_and_preserves_history(self):
        self.mailbox.compute_offer(self.request, now_us=110); self.pump()
        body = self.actor.link.packets[0].payload['body']
        notice = DbAbort('CHECKPOINT', canonical(body), self.request.job.job_id, 100, 1000, 180,
            self.client.endpoint, self.context.sha256, 'D_OWNER_REBOUND_UNKNOWN')
        self.clock.now = 180; self.assertTrue(self.client.receive_abort(notice))
        delivery = self.mailbox.compute_take(current_context_sha256=self.context.sha256, now_us=190)
        self.assertFalse(delivery.current); self.assertIs(delivery.result.original_notice, notice)
        self.assertEqual((self.arena.publishes, self.arena.posts), (0, 0))
        self.pump(); self.assertEqual(self.client.state, 'IDLE')


if __name__ == '__main__':
    unittest.main()
