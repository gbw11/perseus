"""순번 발급·TX 보유의 합성 사건 시험. 실제 송신/attempt·원 STOP 집행·회수 수락은 아니다."""

from dataclasses import FrozenInstanceError, replace
import unittest
from unittest.mock import patch

from lifecycle_draft_fixtures import draft, request
from lifecycle_message_fixtures import packet as message_packet, peer
from xenova_vehicle.runtime import tx_outbox as module
from xenova_vehicle.runtime.lifecycle_messages import LifecycleMessageError, LifecycleReceiver, RxDisposition
from xenova_vehicle.runtime.local_envelope import EnvelopeStructureError
from xenova_vehicle.runtime.mailbox import PendingTransmission, PendingTx
from xenova_vehicle.runtime.tx_outbox import (
    DiscardCause, SequenceExhausted, TxOutbox, TxOutboxError, TxPutResult, TxStatus, _next_after,
)


MAX_U64 = 2**64 - 1


def heartbeat(number, *, state="STARTING", issued=100, expires=200):
    return draft("HEARTBEAT", request_id=request(number), issued=issued, expires=expires,
                 payload={"worker_state": state, "last_progress_sequence": None, "last_progress_j_us": None})


class OutboxAssertions(unittest.TestCase):
    def prepared(self, outbox, *, now=140, cap=None):
        result = outbox.prepare(now_us=now, cap_us=cap)
        self.assertIs(result.status, TxStatus.PREPARED_NEW)
        self.assertIsNotNone(result.handle)
        return result

    def fail_put_unchanged(self, outbox, value, *, now=140, cap=None):
        before = outbox.snapshot
        with self.assertRaises(TxOutboxError):
            outbox.put(value, now_us=now, cap_us=cap)
        self.assertEqual(outbox.snapshot, before)


class QueueAndPriorityTests(OutboxAssertions):
    def test_empty_queue_has_no_sequence_issued_and_snapshot_counts_are_copies(self):
        outbox = TxOutbox(peer(), "S")
        result = outbox.prepare(now_us=100)
        self.assertIs(result.status, TxStatus.EMPTY)
        self.assertIsNone(result.handle)
        self.assertEqual(result.drops, ())
        self.assertEqual(outbox.snapshot.next_sequence, 1)
        self.assertEqual(outbox.encoded_held_bytes, 0)
        counts = outbox.counts
        self.assertEqual(counts, {"general": 0, "terminal": 0, "heartbeat": 0, "pending": 0})
        counts["general"] = 999
        self.assertEqual(outbox.counts["general"], 0)

    def test_general_eight_and_terminal_two_busy_do_not_change_any_other_state(self):
        outbox = TxOutbox(peer(), "S")
        ordinary = [draft(request_id=request(n)) for n in range(1, 9)]
        terminal = [draft("STOPPED", request_id=request(n)) for n in (20, 21)]
        for value in (*ordinary, *terminal, heartbeat(30)):
            self.assertTrue(outbox.put(value, now_us=140))
        before = outbox.snapshot
        self.assertIs(outbox.put(draft(request_id=request(9)), now_us=140), TxPutResult.BUSY_GENERAL)
        self.assertEqual(outbox.snapshot, before)
        self.assertIs(outbox.put(draft("STOPPED", request_id=request(22)), now_us=140), TxPutResult.BUSY_TERMINAL)
        self.assertEqual(outbox.snapshot, before)
        self.assertEqual(outbox.counts, {"general": 8, "terminal": 2, "heartbeat": 1, "pending": 0})
        self.assertEqual(outbox.snapshot.next_sequence, 1)
        self.assertEqual(outbox.encoded_held_bytes, 0)
        # HELLO8개는 자료구조 상한 입력이며 동일 worker의 실제 HELLO8회를 허가하지 않는다.

    def test_terminal_then_general_fifo_then_latest_heartbeat_get_actual_increasing_sequences(self):
        outbox = TxOutbox(peer(), "S")
        general = [draft(request_id=request(n)) for n in (1, 2)]
        terminal = [draft("STOPPED", request_id=request(n)) for n in (3, 4)]
        old_hb, new_hb = heartbeat(5), heartbeat(6)
        for value in (old_hb, general[0], terminal[0], general[1], terminal[1], new_hb):
            outbox.put(value, now_us=140)
        self.assertEqual(outbox.snapshot.next_sequence, 1)
        for sequence, expected in enumerate((*terminal, *general, new_hb), 1):
            result = self.prepared(outbox)
            self.assertIs(outbox.snapshot.pending_draft, expected)
            self.assertEqual(result.handle.packet.fields["request_id"], expected.request_id)
            self.assertEqual(result.handle.packet.fields["message_sequence"], str(sequence))
            self.assertTrue(outbox.finish(result.handle))
        self.assertIs(outbox.prepare(now_us=140).status, TxStatus.EMPTY)
        self.assertEqual(outbox.snapshot.next_sequence, 6)

    def test_heartbeat_ten_thousand_replacements_stay_unsequenced_and_bounded(self):
        outbox = TxOutbox(peer(), "S")
        for number in range(1, 10001):
            value = heartbeat(number)
            outcome = outbox.put(value, now_us=140)
            self.assertIs(outcome, TxPutResult.ACCEPTED if number == 1 else TxPutResult.REPLACED_HEARTBEAT)
        self.assertEqual(outbox.counts, {"general": 0, "terminal": 0, "heartbeat": 1, "pending": 0})
        self.assertEqual(outbox.snapshot.next_sequence, 1)
        self.assertEqual(outbox.encoded_held_bytes, 0)
        self.assertNotIn("message_sequence", outbox.snapshot.heartbeat.fields)
        result = self.prepared(outbox)
        self.assertEqual(result.handle.packet.fields["request_id"], request(10000))
        self.assertEqual(result.handle.packet.fields["message_sequence"], "1")

    def test_terminal_does_not_overtake_an_already_prepared_general_packet(self):
        outbox = TxOutbox(peer(), "S")
        outbox.put(draft(request_id=request(1)), now_us=140)
        first = self.prepared(outbox)
        terminal = draft("STOPPED", request_id=request(2))
        outbox.put(terminal, now_us=140)
        before = outbox.snapshot
        result = outbox.prepare(now_us=140)
        self.assertIs(result.status, TxStatus.PENDING_CURRENT)
        self.assertIs(result.handle, first.handle)
        self.assertEqual(outbox.snapshot, before)
        outbox.finish(first.handle)
        second = self.prepared(outbox)
        self.assertIs(outbox.snapshot.pending_draft, terminal)
        self.assertEqual(second.handle.packet.fields["message_sequence"], "2")


class HeldRequestTests(OutboxAssertions):
    def test_same_value_separate_draft_object_is_duplicate_before_capacity_check(self):
        outbox = TxOutbox(peer(), "S")
        original = draft(request_id=request(1))
        outbox.put(original, now_us=140)
        for number in range(2, 9):
            outbox.put(draft(request_id=request(number)), now_us=140)
        copy = draft(request_id=request(1))
        self.assertIsNot(original, copy)
        before = outbox.snapshot
        self.assertIs(outbox.put(copy, now_us=140), TxPutResult.DUPLICATE_NO_EFFECT)
        self.assertEqual(outbox.snapshot, before)

    def test_pending_heartbeat_duplicate_or_conflict_does_not_erase_queued_new_heartbeat(self):
        outbox = TxOutbox(peer(), "S")
        pending_hb, queued_hb = heartbeat(1), heartbeat(2)
        outbox.put(pending_hb, now_us=140)
        first = self.prepared(outbox)
        outbox.put(queued_hb, now_us=140)
        before = outbox.snapshot
        self.assertIs(outbox.put(heartbeat(1), now_us=140), TxPutResult.DUPLICATE_NO_EFFECT)
        self.assertEqual(outbox.snapshot, before)
        self.fail_put_unchanged(outbox, heartbeat(1, state="STREAMING"))
        self.assertIs(outbox.snapshot.heartbeat, queued_hb)
        self.assertIs(outbox.snapshot.pending, first.handle)

    def test_fingerprint_conflicts_cover_kind_body_times_and_original_cap(self):
        outbox = TxOutbox(peer(), "S")
        outbox.put(heartbeat(1), now_us=140)
        changed = [draft(request_id=request(1)), heartbeat(1, state="DEGRADED"),
                   heartbeat(1, issued=110), heartbeat(1, expires=201),
                   draft("HEARTBEAT", request_id=request(1), cap_us=250)]
        for value in changed:
            with self.subTest(kind=value.kind, issued=value.issued_us, expiry=value.expires_us, cap=value.cap_us):
                self.fail_put_unchanged(outbox, value)

    def test_replaced_or_finished_request_has_no_unbounded_history_or_raw_resend_service(self):
        outbox = TxOutbox(peer(), "S")
        first = heartbeat(1)
        outbox.put(first, now_us=140)
        outbox.put(heartbeat(2), now_us=140)
        self.assertIs(outbox.put(first, now_us=140), TxPutResult.REPLACED_HEARTBEAT)
        prepared = self.prepared(outbox)
        wire = prepared.handle.raw_bytes
        outbox.finish(prepared.handle)
        self.assertIs(outbox.put(first, now_us=140), TxPutResult.ACCEPTED)
        again = self.prepared(outbox)
        self.assertNotEqual(again.handle.raw_bytes, wire)
        self.assertEqual(again.handle.packet.fields["message_sequence"], "2")
        self.assertEqual(again.handle.packet.fields["issued_j_us"], "100")

    def test_lifetime_is_checked_before_exact_held_duplicate(self):
        outbox = TxOutbox(peer(), "S")
        value = draft()
        outbox.put(value, now_us=100)
        self.fail_put_unchanged(outbox, value, now=200)
        self.fail_put_unchanged(outbox, value, now=140, cap=199)
        self.assertIs(outbox.put(value, now_us=199, cap_us=200), TxPutResult.DUPLICATE_NO_EFFECT)


class PreparePlanTests(OutboxAssertions):
    def expired_terminal_and_valid_general(self):
        outbox = TxOutbox(peer(), "S")
        expired = draft("STOPPED", request_id=request(1), expires=130)
        valid = draft(request_id=request(2))
        outbox.put(expired, now_us=100)
        outbox.put(valid, now_us=100)
        return outbox, expired, valid

    def test_encode_failure_preserves_planned_expired_purge_and_every_state(self):
        outbox, expired, valid = self.expired_terminal_and_valid_general()
        before = outbox.snapshot
        with patch.object(module, "encode_structure", side_effect=EnvelopeStructureError("injected encode failure")) as encode:
            with self.assertRaises(ValueError):
                outbox.prepare(now_us=140)
        self.assertEqual(outbox.snapshot, before)
        self.assertEqual(encode.call_count, 1)
        fields = encode.call_args.args[0]
        self.assertEqual(fields["request_id"], valid.request_id)
        self.assertEqual(fields["message_sequence"], "1")
        result = self.prepared(outbox)
        self.assertEqual(len(result.drops), 1)
        self.assertIs(result.drops[0].draft, expired)
        self.assertIs(result.drops[0].cause, DiscardCause.EXPIRED)
        self.assertEqual(outbox.snapshot.next_sequence, 2)

    def test_pending_acceptance_failure_also_preserves_the_whole_plan(self):
        real = PendingTx

        class Reject(real):
            def accept(self, _packet):
                return False

        class NoHandle(real):
            def accept(self, _packet):
                return True

        for holder in (Reject, NoHandle):
            with self.subTest(holder=holder.__name__):
                outbox, _, _ = self.expired_terminal_and_valid_general()
                before = outbox.snapshot
                with patch.object(module, "PendingTx", holder), self.assertRaises(TxOutboxError):
                    outbox.prepare(now_us=140)
                self.assertEqual(outbox.snapshot, before)

    def test_all_eleven_expired_items_are_reported_without_encoding_or_sequence_consumption(self):
        outbox = TxOutbox(peer(), "S")
        general = [draft(request_id=request(n), expires=130) for n in range(1, 9)]
        terminal = [draft("STOPPED", request_id=request(n), expires=130) for n in (9, 10)]
        hb = heartbeat(11, expires=130)
        for value in (*general, *terminal, hb):
            outbox.put(value, now_us=100)
        with patch.object(module, "encode_structure", side_effect=AssertionError("must not encode")):
            result = outbox.prepare(now_us=130)
        self.assertIs(result.status, TxStatus.EMPTY)
        self.assertIsNone(result.handle)
        self.assertEqual([drop.draft for drop in result.drops], [*terminal, *general, hb])
        self.assertTrue(all(drop.cause is DiscardCause.EXPIRED for drop in result.drops))
        self.assertEqual(outbox.snapshot.next_sequence, 1)
        self.assertEqual(outbox.counts, {"general": 0, "terminal": 0, "heartbeat": 0, "pending": 0})

    def test_pending_observation_never_purges_other_queued_items(self):
        outbox = TxOutbox(peer(), "S")
        outbox.put(draft(request_id=request(1)), now_us=100)
        first = self.prepared(outbox, now=100)
        expired = draft("STOPPED", request_id=request(2), expires=130)
        outbox.put(expired, now_us=100)
        before = outbox.snapshot
        result = outbox.prepare(now_us=140)
        self.assertIs(result.status, TxStatus.PENDING_CURRENT)
        self.assertIs(result.handle, first.handle)
        self.assertEqual(result.drops, ())
        self.assertEqual(outbox.snapshot, before)


class PendingAndDiscardTests(OutboxAssertions):
    def test_one_thousand_observations_keep_handle_bytes_times_and_sequence(self):
        outbox = TxOutbox(peer(), "S")
        original = draft(cap_us=250)
        outbox.put(original, now_us=100)
        first = self.prepared(outbox, now=100)
        before = outbox.snapshot
        for _ in range(1000):
            result = outbox.prepare(now_us=140)
            self.assertIs(result.status, TxStatus.PENDING_CURRENT)
            self.assertIs(result.handle, first.handle)
            self.assertIs(result.handle.raw_bytes, first.handle.raw_bytes)
            self.assertEqual(outbox.snapshot, before)
        self.assertEqual(first.handle.packet.fields["issued_j_us"], "100")
        self.assertEqual(first.handle.packet.fields["expires_j_us"], "200")
        self.assertEqual(outbox.snapshot.pending_draft.cap_us, 250)
        self.assertEqual(outbox.encoded_held_bytes, len(first.handle.raw_bytes))

    def test_pending_expiry_and_cap_are_observations_until_exact_abandon(self):
        for now, cap, status, cause in ((199, None, TxStatus.PENDING_CURRENT, None),
                                       (200, None, TxStatus.PENDING_EXPIRED, DiscardCause.EXPIRED),
                                       (201, None, TxStatus.PENDING_EXPIRED, DiscardCause.EXPIRED),
                                       (140, 199, TxStatus.PENDING_CAP_EXCEEDED, DiscardCause.CAP_EXCEEDED)):
            with self.subTest(now=now, cap=cap):
                outbox = TxOutbox(peer(), "S")
                original = draft()
                outbox.put(original, now_us=100)
                first = self.prepared(outbox, now=100)
                before = outbox.snapshot
                result = outbox.prepare(now_us=now, cap_us=cap)
                self.assertIs(result.status, status)
                self.assertIs(result.handle, first.handle)
                self.assertEqual(outbox.snapshot, before)
                if cause is not None:
                    dropped = outbox.abandon(first.handle, cause=cause)
                    self.assertIs(dropped.handle, first.handle)
                    self.assertIs(dropped.draft, original)
                    self.assertIs(dropped.cause, cause)
                    self.assertEqual(outbox.snapshot.next_sequence, 2)
                    self.assertIsNone(outbox.snapshot.pending)
                    self.assertEqual(outbox.encoded_held_bytes, 0)

    def test_abandon_is_not_finish_and_old_or_foreign_handles_cannot_clear_the_next_pending(self):
        outbox = TxOutbox(peer(), "S")
        outbox.put(draft(request_id=request(1)), now_us=100)
        first = self.prepared(outbox, now=100)
        with patch.object(PendingTx, "finish", side_effect=AssertionError("abandon must not call success finish")):
            dropped = outbox.abandon(first.handle, cause=DiscardCause.CANCELLED)
        self.assertIs(dropped.handle, first.handle)
        outbox.put(draft(request_id=request(2)), now_us=140)
        second = self.prepared(outbox)
        self.assertIsNot(first.handle, second.handle)
        self.assertEqual(second.handle.packet.fields["message_sequence"], "2")
        before = outbox.snapshot
        self.assertFalse(outbox.finish(first.handle))
        self.assertIsNone(outbox.abandon(first.handle, cause=DiscardCause.FENCED))
        foreign = PendingTransmission(second.handle.packet)
        self.assertFalse(outbox.finish(foreign))
        self.assertIsNone(outbox.abandon(foreign, cause=DiscardCause.FENCED))
        self.assertEqual(outbox.snapshot, before)
        self.assertTrue(outbox.finish(second.handle))
        self.assertFalse(outbox.finish(second.handle))

    def test_all_abandon_causes_are_explicit_caller_statements_and_wrong_cause_is_atomic(self):
        for cause in DiscardCause:
            with self.subTest(cause=cause):
                outbox = TxOutbox(peer(), "S")
                outbox.put(draft(), now_us=100)
                result = self.prepared(outbox, now=100)
                before = outbox.snapshot
                with self.assertRaises(TxOutboxError):
                    outbox.abandon(result.handle, cause=cause.value)
                self.assertEqual(outbox.snapshot, before)
                self.assertIs(outbox.abandon(result.handle, cause=cause).cause, cause)


class ScopeTimeAndCounterTests(OutboxAssertions):
    def test_put_time_cap_and_wrong_context_errors_do_not_mutate_state(self):
        outbox = TxOutbox(peer(), "S")
        for now, cap in ((99, None), (200, None), (140, 199), (True, None), (140, MAX_U64 + 1)):
            with self.subTest(now=now, cap=cap):
                self.fail_put_unchanged(outbox, draft(), now=now, cap=cap)
        foreign = replace(peer(), channel_epoch=request(99))
        self.fail_put_unchanged(outbox, draft(current=foreign))
        self.fail_put_unchanged(outbox, draft("STOP"))
        self.fail_put_unchanged(outbox, None)
        self.assertEqual(outbox.snapshot.next_sequence, 1)

    def test_queued_cap_drops_without_rewriting_original_packet_fields(self):
        outbox = TxOutbox(peer(), "S")
        value = draft()
        outbox.put(value, now_us=100)
        result = outbox.prepare(now_us=140, cap_us=199)
        self.assertIs(result.status, TxStatus.EMPTY)
        self.assertIs(result.drops[0].draft, value)
        self.assertIs(result.drops[0].cause, DiscardCause.CAP_EXCEEDED)
        self.assertEqual(value.expires_us, 200)
        self.assertEqual(outbox.snapshot.next_sequence, 1)

    def test_prepare_with_future_time_or_bad_now_does_not_commit_a_partial_purge(self):
        outbox = TxOutbox(peer(), "S")
        outbox.put(draft("STOPPED", request_id=request(1), expires=130), now_us=100)
        outbox.put(draft(request_id=request(2), issued=150), now_us=150)
        before = outbox.snapshot
        for now in (140, True, 150.0, -1, MAX_U64 + 1):
            with self.subTest(now=now), self.assertRaises(TxOutboxError):
                outbox.prepare(now_us=now)
            self.assertEqual(outbox.snapshot, before)

    def test_next_sequence_arithmetic_is_bounded_without_production_seed_or_reset(self):
        self.assertEqual(_next_after(1), 2)
        self.assertEqual(_next_after(MAX_U64 - 1), MAX_U64)
        self.assertIsNone(_next_after(MAX_U64))
        for invalid in (0, -1, True, 1.0, MAX_U64 + 1):
            with self.subTest(invalid=invalid), self.assertRaises(TxOutboxError):
                _next_after(invalid)
        with self.assertRaises(TypeError):
            TxOutbox(peer(), "S", next_sequence=MAX_U64)

    def test_test_only_private_counter_injection_exhaustion_preserves_valid_candidate_and_planned_drop(self):
        outbox = TxOutbox(peer(), "S")
        outbox._next_sequence = MAX_U64  # 시험 전용 fault injection. production 시작/복원 API가 아니다.
        outbox.put(draft(request_id=request(1)), now_us=100)
        last = self.prepared(outbox, now=100)
        self.assertEqual(last.handle.packet.fields["message_sequence"], str(MAX_U64))
        self.assertIsNone(outbox.snapshot.next_sequence)
        self.assertIs(outbox.prepare(now_us=100).handle, last.handle)
        outbox.finish(last.handle)
        expired = draft("STOPPED", request_id=request(2), expires=130)
        valid = draft(request_id=request(3))
        outbox.put(expired, now_us=100)
        outbox.put(valid, now_us=100)
        before = outbox.snapshot
        with self.assertRaises(SequenceExhausted):
            outbox.prepare(now_us=140)
        self.assertEqual(outbox.snapshot, before)
        result = outbox.prepare(now_us=200)
        self.assertIs(result.status, TxStatus.EMPTY)
        self.assertEqual(len(result.drops), 2)
        self.assertIsNone(outbox.snapshot.next_sequence)

    def test_new_peer_never_rebases_an_old_draft_or_steals_its_pending_handle(self):
        old = TxOutbox(peer(), "S")
        value = draft()
        old.put(value, now_us=100)
        first = self.prepared(old, now=100)
        current = replace(peer(), channel_epoch=request(99))
        new = TxOutbox(current, "S")
        self.fail_put_unchanged(new, value)
        self.assertFalse(new.finish(first.handle))
        old.abandon(first.handle, cause=DiscardCause.FENCED)
        new.put(draft(current=current), now_us=140)
        result = self.prepared(new)
        self.assertEqual(result.handle.packet.fields["channel_epoch"], current.channel_epoch)
        self.assertEqual(result.handle.packet.fields["message_sequence"], "1")

    def test_snapshot_and_returned_field_copies_cannot_change_held_intent(self):
        outbox = TxOutbox(peer(), "S")
        value = draft()
        outbox.put(value, now_us=100)
        before = outbox.snapshot
        with self.assertRaises(FrozenInstanceError):
            before.next_sequence = 999
        value.payload["capability_mask"] = "1"
        value.fields["issued_j_us"] = "999"
        before.general[0].fields["payload"]["role"] = "D"
        self.assertEqual(outbox.snapshot, before)
        result = self.prepared(outbox, now=100)
        result.handle.packet.fields["expires_j_us"] = "999999"
        self.assertEqual(result.handle.packet.fields["issued_j_us"], "100")
        self.assertEqual(result.handle.packet.fields["expires_j_us"], "200")


class B2HandoffTests(OutboxAssertions):
    def test_prepared_stop_intent_then_lower120_report125_register131_receive140(self):
        current = peer()
        outbox, receiver = TxOutbox(current, current.role), LifecycleReceiver(current, "S")
        outbox.put(draft("STOP", current=current), now_us=100)
        prepared = self.prepared(outbox, now=100)
        self.assertEqual(receiver.snapshot.highwater, 0)
        self.assertTrue(receiver.register_stop_intent(prepared.handle.packet, now_us=100))
        self.assertIsNone(receiver.snapshot.pending_stop.first_sent_us)
        self.assertEqual(receiver.snapshot.highwater, 0)
        receiver.record_first_sent(prepared.handle.packet, 120, now_us=131)  # 외부 full-send 성공의 합성 fact다.
        outbox.finish(prepared.handle)
        report = message_packet("STOPPED", current=current, issued=125)
        accepted = receiver.receive(report, now_us=140)
        self.assertIs(accepted.disposition, RxDisposition.ACCEPTED_NEW)
        self.assertFalse(receiver.snapshot.hello_seen)
        self.assertEqual(receiver.snapshot.pending_stop.first_sent_us, 120)

    def test_abandoned_stop_keeps_b2_latch_and_reprepared_new_sequence_is_not_a_retry(self):
        current = peer()
        outbox, receiver = TxOutbox(current, current.role), LifecycleReceiver(current, "S")
        value = draft("STOP", current=current)
        outbox.put(value, now_us=100)
        first = self.prepared(outbox, now=100)
        receiver.register_stop_intent(first.handle.packet, now_us=100)
        before = receiver.snapshot
        outbox.abandon(first.handle, cause=DiscardCause.CANCELLED)
        self.assertEqual(receiver.snapshot, before)
        outbox.put(value, now_us=140)
        again = self.prepared(outbox)
        self.assertEqual(again.handle.packet.fields["message_sequence"], "2")
        self.assertNotEqual(first.handle.raw_bytes, again.handle.raw_bytes)
        with self.assertRaises(LifecycleMessageError):
            receiver.register_stop_intent(again.handle.packet, now_us=140)
        self.assertEqual(receiver.snapshot, before)  # 이 경우 caller는 새 packet을 송신하지 않는다.

    def test_late_actual_full_send_bookkeeping_is_not_an_unsent_expiry_drop(self):
        current = peer()
        outbox, receiver = TxOutbox(current, current.role), LifecycleReceiver(current, "S")
        outbox.put(draft("STOP", current=current), now_us=100)
        prepared = self.prepared(outbox, now=100)
        receiver.register_stop_intent(prepared.handle.packet, now_us=100)
        self.assertIs(outbox.prepare(now_us=200).status, TxStatus.PENDING_EXPIRED)
        with self.assertRaises(LifecycleMessageError):
            receiver.record_first_sent(prepared.handle.packet, 199, now_us=200)
        self.assertTrue(outbox.finish(prepared.handle))  # 합성 full-send 성공 결과의 local 정리만 한다.
        self.assertIsNone(receiver.snapshot.pending_stop.first_sent_us)
        self.assertEqual(receiver.snapshot.pending_stop.message.packet.raw_bytes, prepared.handle.raw_bytes)
        self.assertEqual(outbox.encoded_held_bytes, 0)

    def test_prior_hello_then_stopped_still_allows_heartbeat_report_in_b2(self):
        current = peer()
        outbox, receiver = TxOutbox(current, "S"), LifecycleReceiver(current, "S")
        outbox.put(draft(request_id=request(1)), now_us=100)
        hello = self.prepared(outbox, now=100)
        receiver.receive(hello.handle.packet, now_us=100)
        outbox.finish(hello.handle)
        stop = message_packet("STOP", current=current, request_id=request(4))
        receiver.register_stop_intent(stop, now_us=100)
        receiver.record_first_sent(stop, 120, now_us=131)
        outbox.put(draft("STOPPED", request_id=request(4), issued=125), now_us=140)
        stopped = self.prepared(outbox)
        receiver.receive(stopped.handle.packet, now_us=140)
        outbox.finish(stopped.handle)
        outbox.put(heartbeat(5, issued=140), now_us=140)
        hb = self.prepared(outbox)
        accepted = receiver.receive(hb.handle.packet, now_us=140)
        self.assertIs(accepted.disposition, RxDisposition.ACCEPTED_NEW)
        self.assertEqual(accepted.message.kind, "HEARTBEAT")
        self.assertEqual(receiver.snapshot.highwater, 3)
        self.assertIsNotNone(receiver.snapshot.stopped_report)


if __name__ == "__main__":
    unittest.main()
