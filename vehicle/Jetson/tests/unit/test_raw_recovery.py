"""원 PAUSE/실종료/정규화에 결박한 M-only handover의 핵심 경계를 검사한다.

wire와 원장/합성 token 대역 시험이다. 실제 process exit·Device 유지·Linux token
정규화 성공은 root의 Nano 통합에서 별도로 확인한다.
"""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from raw_lease_fixtures import identifier
from slot_message_fixtures import packet, reader_context, slot_fields, writer_context
from test_resident_broker import Link, Token
from xenova_vehicle.runtime.raw_leases import (Endpoint, FilledInfo, LeaseLedgerError, LeaseRequest,
    RawLeaseLedger, RawOwnerExitBarrier, RawOwnerExitEvidence, RawRecoveryBarrier, RawRecoveryEvidence,
    RemoteReturnStatement, SlotState)
from xenova_vehicle.runtime.raw_recovery import RawControlError, RawControlMessage, control_payload
from xenova_vehicle.runtime.resident_broker import BrokerError, ResidentRawBroker
from xenova_vehicle.runtime.resident_transport import SentPacket
from xenova_vehicle.runtime.resident_workers import _Actor
from xenova_vehicle.runtime.slot_messages import SlotMessage, SlotTimeLimits


def control(writer, op='PAUSE', parent=None, *, issued=10000020, expires=10001000, request_id=None):
    peer = writer.bind.context.peer
    pause_id = identifier(701) if parent is None else parent.pause_request_id
    request_id = (identifier(702) if op == 'RESUME' else pause_id) if request_id is None else request_id
    value = dict(version=1, kind='RAW_CONTROL', binding_domain='RAW',
        binding_revision=str(writer.bind.binding_revision), service_instance=peer.service_instance,
        channel_epoch=peer.channel_epoch, sender_instance=peer.worker_instance if op == 'PAUSED' else peer.service_instance,
        message_sequence='800', request_id=request_id, issued_j_us=str(issued), expires_j_us=str(expires),
        payload=control_payload(op, writer.bind, pause_id))
    return RawControlMessage(packet(value), writer.bind, parent)


class RawRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.writer = writer_context()
        self.reader = reader_context(self.writer)
        self.new_reader = Endpoint(identifier(6), identifier(7))

    def barrier(self, *, scope=None):
        return RawRecoveryBarrier(self.writer.scope if scope is None else scope, self.reader.reader,
                                  identifier(701), 'e' * 64, 10000040, 10000030)

    def test_pause_ack_and_distinct_resume_keep_original_parent_after_pause_packet_expires(self):
        pause = control(self.writer)
        paused = control(self.writer, 'PAUSED', pause, issued=10000040)
        resume = control(self.writer, 'RESUME', paused, issued=10002000, expires=10003000)
        self.assertIs(paused.parent, pause)
        self.assertIs(resume.parent, paused)
        self.assertEqual(resume.pause_request_id, pause.request_id)
        self.assertNotEqual(resume.request_id, pause.request_id)
        resume.check_live(10002001)
        with self.assertRaises(RawControlError):
            pause.check_live(10002001)

    def test_paused_cannot_extend_original_expiry_or_change_current_a_channel(self):
        pause = control(self.writer)
        with self.assertRaises(RawControlError):
            control(self.writer, 'PAUSED', pause, issued=10000040, expires=pause.expires_us + 1)
        paused = control(self.writer, 'PAUSED', pause, issued=10000040)
        fields = paused.packet.fields
        fields['channel_epoch'] = identifier(888)
        with self.assertRaises(RawControlError):
            RawControlMessage(packet(fields), self.writer.bind, pause)
        with self.assertRaises(RawControlError):
            control(self.writer, 'RESUME', pause, issued=10000050)

    def test_resume_cannot_reuse_pause_request_or_another_pause_parent(self):
        pause = control(self.writer)
        paused = control(self.writer, 'PAUSED', pause, issued=10000040)
        with self.assertRaises(RawControlError):
            control(self.writer, 'RESUME', paused, issued=10000050, request_id=pause.request_id)
        fields = control(self.writer, 'RESUME', paused, issued=10000050).packet.fields
        fields['payload']['pause_request_id'] = identifier(999)
        with self.assertRaises(RawControlError):
            RawControlMessage(packet(fields), self.writer.bind, paused)

    def test_evidence_accepts_old_m_exit_before_pause_and_requires_normalization_after_both(self):
        barrier = self.barrier()
        evidence = RawRecoveryEvidence(barrier, identifier(703), 10000050)
        self.assertIs(evidence.barrier, barrier)
        with self.assertRaises(LeaseLedgerError):
            RawRecoveryEvidence(barrier, identifier(703), 10000039)
        owner = RawOwnerExitBarrier(self.writer.scope, 10000060, 10000030)
        self.assertIsInstance(RawOwnerExitEvidence(owner, identifier(704), 10000060), RawOwnerExitEvidence)
        with self.assertRaises(LeaseLedgerError):
            RawRecoveryEvidence(owner, identifier(703), 10000060)

    def test_ledger_rebind_preserves_generation_and_old_release_cannot_free_new_writer(self):
        ledger = RawLeaseLedger(self.writer.scope, self.reader.reader)
        grant = ledger.reserve_write(0, LeaseRequest(identifier(801), 10000000, 10001000),
                                     identifier(802), now_us=10000001).value
        info = FilledInfo(grant.key, 960000, 'c' * 64, 10000010)
        ledger.filled(info, now_us=10000010)
        from xenova_vehicle.runtime.raw_leases import ReadRequest
        request = ReadRequest(ledger.offer(0, now_us=10000011), self.reader.reader,
                              LeaseRequest(identifier(803), 10000012, 10000100))
        old = ledger.acquire(request, identifier(804), now_us=10000013).value
        evidence = RawRecoveryEvidence(self.barrier(), identifier(703), 10000050)
        self.assertTrue(ledger.rebind_reader(self.new_reader, evidence, now_us=10000051))
        self.assertFalse(ledger.rebind_reader(self.new_reader, evidence, now_us=10000052))
        self.assertEqual(ledger.snapshot.reader, self.new_reader)
        self.assertEqual((ledger.snapshot.slots[0].generation, ledger.snapshot.slots[0].state), (1, SlotState.FREE))
        new = ledger.reserve_write(0, LeaseRequest(identifier(805), 10000060, 10001000),
                                   identifier(806), now_us=10000061).value
        ledger.release_remote(RemoteReturnStatement(old, 10000040), now_us=10000062)
        self.assertEqual(ledger.snapshot.slots[0].write, new)
        self.assertIs(ledger.snapshot.slots[0].state, SlotState.WRITING)
        self.assertEqual(new.key.generation, 2)

    def test_ledger_rebind_rejects_writing_and_foreign_scope_without_changing_state(self):
        ledger = RawLeaseLedger(self.writer.scope, self.reader.reader)
        ledger.reserve_write(0, LeaseRequest(identifier(801), 10000000, 10001000),
                             identifier(802), now_us=10000001)
        before = ledger.snapshot
        with self.assertRaises(LeaseLedgerError):
            ledger.rebind_reader(self.new_reader, RawRecoveryEvidence(self.barrier(), identifier(703), 10000050),
                                 now_us=10000051)
        self.assertEqual(ledger.snapshot, before)
        other = replace(self.writer.scope, arena_id=identifier(999))
        empty = RawLeaseLedger(self.writer.scope, self.reader.reader)
        with self.assertRaises(LeaseLedgerError):
            empty.rebind_reader(self.new_reader,
                RawRecoveryEvidence(self.barrier(scope=other), identifier(703), 10000050), now_us=10000051)

    def broker(self):
        tokens = tuple((Token(1), Token(0)) for _ in range(2))
        selected = SlotTimeLimits(1000, 500, 150)
        return ResidentRawBroker(self.writer.bind, self.writer.scope, self.reader.reader, tokens, limits=selected), tokens

    def test_pause_waits_for_reserved_grant_selection_and_actual_filled_before_rebind(self):
        broker, tokens = self.broker()
        a = Link(self.writer)
        a.busy = True
        request_packet = packet(slot_fields('REQUEST', self.writer, issued=10000000, expires=10001000))
        request = SlotMessage(request_packet, self.writer, limits=broker.limits)
        broker.receive('A', request_packet, now_us=10000000)
        broker.pump(a, now_us=10000001)
        broker.begin_pause()
        self.assertFalse(broker.pause_ready)
        a.busy = False
        broker.pump(a, now_us=10000005)
        self.assertTrue(broker.pause_ready)
        grant = SlotMessage(a.sent[-1], self.writer, request, limits=broker.limits)
        pause = control(self.writer)
        paused = control(self.writer, 'PAUSED', pause, issued=10000040)
        with self.assertRaises(BrokerError):
            broker.accept_paused(paused.packet, pause, first_sent_us=10000021, now_us=10000041)
        values = grant.fields
        values.update(sender_instance=self.writer.scope.writer.worker_instance, issued_j_us='10000010')
        values['payload'].update(op='FILLED', payload_length=960000, descriptor_sha256='c' * 64)
        broker.receive('A', packet(values), now_us=10000010)
        broker.accept_paused(paused.packet, pause, first_sent_us=10000021, now_us=10000041)
        barrier = broker.recovery_barrier(10000030)
        self.assertEqual(barrier.pause_ack_sha256, hashlib.sha256(paused.raw).hexdigest())
        calls = [(token.acquires, token.posts) for pair in tokens for token in pair]
        evidence = RawRecoveryEvidence(barrier, identifier(703), 10000050)
        self.assertTrue(broker.rebind_reader(self.new_reader, evidence, now_us=10000051))
        self.assertEqual([(token.acquires, token.posts) for pair in tokens for token in pair], calls)
        self.assertEqual(broker.snapshot()['slots'][0]['generation'], 1)
        self.assertEqual(broker.snapshot()['slots'][0]['state'], 'FREE')
        new_context = reader_context(self.writer, mapper_number=7, channel_number=6)
        broker.bind_reader(new_context.bind)
        broker.resume()
        self.assertFalse(broker.snapshot()['write_paused'])

    def test_new_requests_are_not_reserved_during_pause_and_wrong_ack_evidence_is_rejected(self):
        broker, _ = self.broker()
        broker.begin_pause()
        values = slot_fields('REQUEST', self.writer, issued=10000000, expires=10001000)
        broker.receive('A', packet(values), now_us=10000000)
        self.assertFalse(broker.snapshot()['write_request_pending'])
        self.assertEqual([s['generation'] for s in broker.snapshot()['slots']], [0, 0])
        pause = control(self.writer)
        paused = control(self.writer, 'PAUSED', pause, issued=10000040)
        broker.accept_paused(paused.packet, pause, first_sent_us=10000021, now_us=10000041)
        barrier = replace(broker.recovery_barrier(10000030), pause_ack_sha256='0' * 64)
        with self.assertRaises(BrokerError):
            broker.rebind_reader(self.new_reader, RawRecoveryEvidence(barrier, identifier(703), 10000050),
                                 now_us=10000051)
        self.assertTrue(broker.snapshot()['write_paused'])
        self.assertEqual([s['generation'] for s in broker.snapshot()['slots']], [0, 0])

    def test_actor_tracks_actual_filled_send_separately_from_queued_data(self):
        actor = object.__new__(_Actor)
        actor.ack = None
        actor.last_filled_sent = None
        actor.link = Mock()
        values = slot_fields('FILLED', self.writer)
        filled = packet(values)
        actor.link.take_sent.return_value = ()
        actor.drain_sent()
        self.assertIsNone(actor.last_filled_sent)
        actor.link.take_sent.return_value = (SentPacket(filled, 10000050, 10000049),)
        actor.drain_sent()
        self.assertIs(actor.last_filled_sent, filled)

    def test_fault_retry_scope_distinguishes_configuration_camera_and_mapper(self):
        cases = [('A', 'ASSET_CHECK', ValueError(), 'NO_RETRY'),
                 ('A', 'STREAMING', TimeoutError(), 'RESTART_CAMERA'),
                 ('M', 'WAIT_CLOCK', ValueError(), 'RESTART_MAPPER')]
        for role, state, error, retry in cases:
            with self.subTest(role=role, state=state):
                actor = object.__new__(_Actor)
                actor.boot = SimpleNamespace(peer=self.writer.bind.context.peer if role == 'A'
                                            else self.reader.bind.context.peer, raw_scope=self.writer.scope)
                actor.state = state
                actor.link = Mock()
                actor.drain_sent = Mock()
                actor.save = Mock()
                actor.fault(error)
                payload = actor.link.send.call_args.args[1]
                self.assertEqual(set(payload), {'domain', 'code', 'last_source_sequence',
                                               'retry_class', 'origin_context_sha256'})
                self.assertEqual(payload['retry_class'], retry)
                self.assertEqual(payload['origin_context_sha256'], self.writer.scope.raw_binding_sha256)

    def test_worker_failure_file_preserves_original_message_and_phase_without_changing_fault_wire(self):
        with tempfile.TemporaryDirectory() as directory:
            actor = object.__new__(_Actor)
            actor.boot = SimpleNamespace(peer=self.writer.bind.context.peer, raw_scope=self.writer.scope,
                                        diagnostic_directory=directory)
            actor.state = 'WARMUP'
            actor.link = Mock()
            actor.drain_sent = Mock()
            actor.fault(RuntimeError('expired SDK/J clock sample'))
            raw = (Path(directory) / 'failure.json').read_bytes()
            saved = json.loads(raw)
            self.assertEqual(saved['exception_type'], 'RuntimeError')
            self.assertEqual(saved['message'], 'expired SDK/J clock sample')
            self.assertEqual(saved['phase'], 'WARMUP')
            self.assertFalse(saved['message_truncated'])
            self.assertEqual(saved['worker_instance'], self.writer.scope.writer.worker_instance)
            self.assertLess(len(raw), 16384)
            self.assertEqual(actor.state, 'FAILED')
            self.assertEqual(set(actor.link.send.call_args.args[1]), {'domain', 'code', 'last_source_sequence',
                'retry_class', 'origin_context_sha256'})

    def test_failure_message_is_bounded_and_diagnostic_io_failure_does_not_hide_original_fault(self):
        class UnformattableError(RuntimeError):
            def __str__(self):
                raise AssertionError('diagnostic must not format arbitrary exception')

        actor = object.__new__(_Actor)
        actor.boot = SimpleNamespace(peer=self.writer.bind.context.peer, raw_scope=self.writer.scope)
        actor.state = 'STREAMING'
        actor.link = Mock()
        actor.drain_sent = Mock()
        actor.save = Mock(side_effect=OSError('diagnostic unavailable'))
        actor.fault(UnformattableError('x' * 100000, object()))
        filename, saved = actor.save.call_args.args
        self.assertEqual(filename, 'failure.json')
        self.assertEqual(saved['phase'], 'STREAMING')
        self.assertEqual(saved['exception_type'], 'UnformattableError')
        self.assertEqual(len(saved['message']), 1024)
        self.assertTrue(saved['message_truncated'])
        self.assertEqual(actor.failure, 'UnformattableError')
        self.assertEqual(actor.failure_diagnostic_error, 'OSError')
        self.assertEqual(actor.link.send.call_count, 1)
        self.assertEqual(actor.link.send.call_args.args[1]['retry_class'], 'RESTART_CAMERA')


if __name__ == '__main__':
    unittest.main()
