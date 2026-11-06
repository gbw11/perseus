"""RX staging/TX 보유의 합성 사건 시험. typed admission·peer·실제 송신·lease 성공을 뜻하지 않는다."""

from dataclasses import FrozenInstanceError
import json
import unittest

from envelope_fixtures import fields, raw
from xenova_vehicle.runtime.local_envelope import decode_structure
from xenova_vehicle.runtime.mailbox import PendingTransmission, PendingTx, PutResult, RxMailbox


def packet(kind="PROGRESS", sequence=1, *, payload=None):
    return decode_structure(raw(kind, sequence, payload=payload))


def maximum_packet(kind, sequence):
    base = raw(kind, sequence, payload={"padding": ""})
    result = packet(kind, sequence, payload={"padding": "x" * (4096 - len(base))})
    if len(result.raw_bytes) != 4096:
        raise AssertionError("test fixture is not an exact 4096B encoded packet")
    return result


def drain(mailbox):
    result = []
    while (value := mailbox.pop()) is not None:
        result.append(value)
    return result


class RxMailboxTests(unittest.TestCase):
    def test_empty_mailbox_and_counts_are_independent_snapshots(self):
        mailbox = RxMailbox()
        self.assertIsNone(mailbox.pop())
        self.assertEqual(mailbox.held_bytes, 0)
        counts = mailbox.counts
        self.assertEqual(counts, {"general": 0, "heartbeat": 0, "terminal": 0})
        counts["general"] = 999
        counts.clear()
        self.assertEqual(mailbox.counts, {"general": 0, "heartbeat": 0, "terminal": 0})
        self.assertIsNot(mailbox.counts, mailbox.counts)

    def test_all_three_terminal_kinds_are_processed_before_earlier_general(self):
        for kind in ("FAULT", "STOP", "STOPPED"):
            with self.subTest(kind=kind):
                mailbox = RxMailbox()
                general, heartbeat, terminal = packet("HELLO"), packet("HEARTBEAT", 2), packet(kind, 3)
                mailbox.put(general)
                mailbox.put(heartbeat)
                mailbox.put(terminal)
                self.assertEqual(mailbox.counts, {"general": 1, "heartbeat": 1, "terminal": 1})
                self.assertEqual(drain(mailbox), [terminal, general, heartbeat])

    def test_other_kinds_share_general_fifo_without_typed_payload_admission(self):
        mailbox = RxMailbox()
        ordinary = [packet(kind, sequence, payload={"opaque_untyped": [True, None]})
                    for sequence, kind in enumerate(("HELLO", "BIND", "BIND_ACK", "PROGRESS", "SLOT", "DB_JOB"), 1)]
        for value in ordinary:
            self.assertIs(mailbox.put(value), PutResult.ACCEPTED)
        self.assertEqual(mailbox.counts["general"], 6)
        self.assertEqual(drain(mailbox), ordinary)

    def test_processing_priority_preserves_each_fifo_but_is_not_sequence_admission(self):
        mailbox = RxMailbox()
        # 큰 seq 뒤에 작은 seq를 넣는다. 여기서는 b2 admission을 대신하지 않는다.
        hb_old, hb_latest = packet("HEARTBEAT", 99), packet("HEARTBEAT", 1)
        first, second = packet("PROGRESS", 8), packet("PROGRESS", 2)
        terminal_first, terminal_second = packet("FAULT", 10), packet("STOP", 3)
        for value in (hb_old, first, terminal_first, second, terminal_second, hb_latest):
            self.assertTrue(mailbox.put(value))
        self.assertEqual(drain(mailbox), [terminal_first, terminal_second, first, second, hb_latest])
        self.assertIsNone(mailbox.pop())

    def test_general_eight_busy_does_not_overwrite_or_block_other_classes(self):
        mailbox = RxMailbox()
        accepted = [packet(sequence=sequence) for sequence in range(1, 9)]
        for value in accepted:
            self.assertTrue(mailbox.put(value))
        before = (mailbox.counts, mailbox.held_bytes)
        for _ in range(20):
            result = mailbox.put(packet(sequence=9))
            self.assertIs(result, PutResult.BUSY_GENERAL)
            self.assertFalse(result.accepted)
            self.assertFalse(result)
            self.assertEqual((mailbox.counts, mailbox.held_bytes), before)
        heartbeat, terminal = packet("HEARTBEAT", 10), packet("FAULT", 11)
        self.assertTrue(mailbox.put(heartbeat))
        self.assertTrue(mailbox.put(terminal))
        self.assertEqual(mailbox.counts, {"general": 8, "heartbeat": 1, "terminal": 1})
        self.assertEqual(drain(mailbox), [terminal, *accepted, heartbeat])

    def test_terminal_two_busy_never_evicts_an_accepted_fault_or_stop(self):
        mailbox = RxMailbox()
        first, second, rejected = packet("FAULT"), packet("STOP", 2), packet("STOPPED", 3)
        mailbox.put(first)
        mailbox.put(second)
        before = (mailbox.counts, mailbox.held_bytes)
        self.assertIs(mailbox.put(rejected), PutResult.BUSY_TERMINAL)
        self.assertEqual((mailbox.counts, mailbox.held_bytes), before)
        general, heartbeat = packet("PROGRESS", 4), packet("HEARTBEAT", 5)
        mailbox.put(general)
        mailbox.put(heartbeat)
        self.assertIs(mailbox.pop(), first)
        self.assertTrue(mailbox.put(rejected))
        self.assertEqual(drain(mailbox), [second, rejected, general, heartbeat])

    def test_latest_heartbeat_replaces_only_heartbeat_and_updates_exact_held_bytes(self):
        mailbox = RxMailbox()
        ordinary = [packet(sequence=value) for value in range(1, 9)]
        terminal = [packet("FAULT", 9), packet("STOP", 10)]
        for value in (*ordinary, *terminal):
            mailbox.put(value)
        first = packet("HEARTBEAT", 11, payload={"padding": "x" * 100})
        replacement = packet("HEARTBEAT", 12)
        self.assertIs(mailbox.put(first), PutResult.ACCEPTED)
        before = mailbox.held_bytes
        self.assertIs(mailbox.put(replacement), PutResult.REPLACED_HEARTBEAT)
        self.assertEqual(mailbox.held_bytes, before - len(first.raw_bytes) + len(replacement.raw_bytes))
        self.assertEqual(mailbox.counts, {"general": 8, "heartbeat": 1, "terminal": 2})
        self.assertEqual(drain(mailbox), [*terminal, *ordinary, replacement])

    def test_general_slot_is_reusable_after_pop_and_tail_order_is_preserved(self):
        mailbox = RxMailbox()
        values = [packet(sequence=value) for value in range(1, 10)]
        for value in values[:8]:
            mailbox.put(value)
        self.assertFalse(mailbox.put(values[8]))
        self.assertIs(mailbox.pop(), values[0])
        self.assertTrue(mailbox.put(values[8]))
        self.assertEqual(drain(mailbox), values[1:])

    def test_same_structural_packet_is_not_logically_deduplicated_by_staging(self):
        mailbox = RxMailbox()
        value = packet("SLOT", payload={"op": "REQUEST"})
        self.assertTrue(mailbox.put(value))
        self.assertTrue(mailbox.put(value))
        self.assertEqual(mailbox.counts["general"], 2)
        self.assertIs(mailbox.pop(), value)
        self.assertIs(mailbox.pop(), value)
        # outstanding producer1과 lease 발급은 후속 admission/ledger의 책임이다.

    def test_caller_fields_payload_and_counts_mutation_cannot_change_queued_packet(self):
        payload = {"nested": {"items": ["original", {"flag": True}]}}
        incoming = fields("PROGRESS", 7, payload=payload)
        wire = raw("PROGRESS", 7, payload=incoming["payload"])
        value = decode_structure(wire)
        mailbox = RxMailbox()
        mailbox.put(value)
        incoming["kind"] = "FAULT"
        incoming["payload"]["nested"]["items"].clear()
        exposed = value.fields
        exposed["kind"] = "HEARTBEAT"
        exposed["payload"]["nested"]["items"][0] = "mutated"
        exposed_payload = value.payload
        exposed_payload["nested"]["items"][1]["flag"] = False
        snapshot = mailbox.counts
        snapshot["terminal"] = 999
        self.assertEqual(mailbox.counts, {"general": 1, "heartbeat": 0, "terminal": 0})
        output = mailbox.pop()
        self.assertEqual(output.kind, "PROGRESS")
        self.assertEqual(output.payload, {"nested": {"items": ["original", {"flag": True}]}})
        self.assertIs(output.raw_bytes, wire)
        self.assertEqual(mailbox.held_bytes, 0)

    def test_wrong_input_type_is_an_error_even_when_general_is_full(self):
        mailbox = RxMailbox()
        for index in range(8):
            mailbox.put(packet(sequence=index + 1))
        before = (mailbox.counts, mailbox.held_bytes)
        for value in (None, True, {}, raw(), "packet"):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                mailbox.put(value)
        self.assertEqual((mailbox.counts, mailbox.held_bytes), before)

    def test_put_result_truthiness_reports_acceptance_only(self):
        for result in PutResult:
            expected = result in (PutResult.ACCEPTED, PutResult.REPLACED_HEARTBEAT)
            with self.subTest(result=result):
                self.assertIs(result.accepted, expected)
                self.assertIs(bool(result), expected)


class PendingTxTests(unittest.TestCase):
    def test_empty_accept_busy_and_exact_finish_are_separate_operations(self):
        pending = PendingTx()
        self.assertIsNone(pending.peek())
        self.assertEqual(pending.held_bytes, 0)
        self.assertFalse(pending.finish(None))
        value, other = packet("BIND"), packet("STOP", 2)
        self.assertTrue(pending.accept(value))
        handle = pending.peek()
        self.assertIs(handle.packet, value)
        self.assertIs(handle.raw_bytes, value.raw_bytes)
        self.assertFalse(pending.accept(other))
        self.assertIs(pending.peek(), handle)
        self.assertFalse(pending.finish(value))
        self.assertFalse(pending.finish(PendingTransmission(value)))
        self.assertEqual(pending.held_bytes, len(value.raw_bytes))
        self.assertTrue(pending.finish(handle))
        self.assertEqual(pending.held_bytes, 0)
        self.assertIsNone(pending.peek())
        self.assertFalse(pending.finish(handle))

    def test_repeated_peek_during_assumed_eagain_keeps_original_noncanonical_wire_bytes(self):
        original = fields("BIND", 77, payload={"opaque": "한글", "items": [1, 2]})
        wire = json.dumps(original, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8")
        self.assertNotEqual(wire, raw("BIND", 77, payload=original["payload"]))
        value = decode_structure(wire)
        pending = PendingTx()
        pending.accept(value)
        handle = pending.peek()
        for _ in range(100):
            self.assertIs(pending.peek(), handle)  # 실제 socket/EAGAIN을 실행한 시험은 아니다.
            self.assertIs(pending.peek().raw_bytes, wire)
            self.assertEqual(pending.peek().packet.fields, original)
            self.assertEqual(pending.held_bytes, len(wire))
        self.assertTrue(pending.finish(handle))

    def test_old_handle_cannot_finish_reaccepted_identical_packet_aba(self):
        pending = PendingTx()
        value = packet("BIND")
        self.assertTrue(pending.accept(value))
        first = pending.peek()
        self.assertTrue(pending.finish(first))
        self.assertTrue(pending.accept(value))
        second = pending.peek()
        self.assertIsNot(first, second)
        self.assertEqual(first, second)  # 같은 값이어도 이전 접수 identity는 현재 접수를 끝낼 수 없다.
        self.assertFalse(pending.finish(first))
        self.assertIs(pending.peek(), second)
        self.assertEqual(pending.held_bytes, len(value.raw_bytes))
        self.assertTrue(pending.finish(second))

    def test_frozen_handle_and_returned_json_copies_cannot_replace_pending_bytes(self):
        pending = PendingTx()
        value = packet("BIND", payload={"nested": {"rows": [1, 2]}})
        pending.accept(value)
        handle = pending.peek()
        wire = handle.raw_bytes
        copied = handle.packet.fields
        copied["expires_j_us"] = "999999"
        copied["payload"]["nested"]["rows"].append(3)
        handle.packet.payload["nested"]["rows"].clear()
        with self.assertRaises(FrozenInstanceError):
            handle.packet = packet("FAULT")
        with self.assertRaises(FrozenInstanceError):
            handle.packet.raw_bytes = b"changed"
        self.assertIs(pending.peek().raw_bytes, wire)
        self.assertEqual(pending.peek().packet.fields["expires_j_us"], "200")
        self.assertEqual(pending.peek().packet.payload, {"nested": {"rows": [1, 2]}})

    def test_rx_busy_terminal_and_priority_processing_do_not_touch_pending_tx(self):
        mailbox, pending = RxMailbox(), PendingTx()
        value = packet("BIND")
        pending.accept(value)
        handle = pending.peek()
        terminal = [packet("FAULT", 2), packet("STOP", 3), packet("STOPPED", 4)]
        mailbox.put(terminal[0])
        mailbox.put(terminal[1])
        self.assertIs(mailbox.put(terminal[2]), PutResult.BUSY_TERMINAL)
        self.assertFalse(pending.accept(terminal[2]))
        self.assertEqual(drain(mailbox), terminal[:2])
        self.assertIs(pending.peek(), handle)
        self.assertIs(pending.peek().packet, value)
        self.assertTrue(pending.finish(handle))

    def test_holder_has_no_expiry_or_payload_permission_side_effect(self):
        data = fields("SLOT", 1, payload={"op": "RELEASE", "untyped": True})
        data.update(issued_j_us="200", expires_j_us="100")
        # issued>expires도 구조만 통과한다. 실제 expiry/cleanup admission은 b2에 남긴다.
        value = decode_structure(json.dumps(data, separators=(",", ":")).encode("utf-8"))
        pending, mailbox = PendingTx(), RxMailbox()
        self.assertTrue(pending.accept(value))
        self.assertTrue(mailbox.put(value))
        self.assertIs(pending.peek().packet, value)
        self.assertEqual(mailbox.pop().payload, {"op": "RELEASE", "untyped": True})
        self.assertIsNotNone(pending.peek())

    def test_invalid_packet_type_is_not_busy_and_cannot_modify_current_pending(self):
        pending = PendingTx()
        pending.accept(packet())
        current = pending.peek()
        for value in (None, {}, raw(), False):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                pending.accept(value)
        self.assertIs(pending.peek(), current)


class EncodedByteBudgetTests(unittest.TestCase):
    def test_eleven_rx_plus_one_tx_maximum_packets_hold_49152_encoded_bytes_not_rss(self):
        mailbox, pending = RxMailbox(), PendingTx()
        ordinary = [maximum_packet("PROGRESS", sequence) for sequence in range(1, 9)]
        heartbeat = maximum_packet("HEARTBEAT", 9)
        terminals = [maximum_packet("FAULT", 10), maximum_packet("STOPPED", 11)]
        transmit = maximum_packet("BIND", 12)
        for value in (*ordinary, heartbeat, *terminals):
            self.assertTrue(mailbox.put(value))
        self.assertTrue(pending.accept(transmit))
        self.assertEqual(mailbox.counts, {"general": 8, "heartbeat": 1, "terminal": 2})
        self.assertEqual(mailbox.held_bytes, 11 * 4096)
        self.assertEqual(pending.held_bytes, 4096)
        self.assertEqual(mailbox.held_bytes + pending.held_bytes, 49152)
        self.assertIs(mailbox.put(maximum_packet("PROGRESS", 13)), PutResult.BUSY_GENERAL)
        self.assertIs(mailbox.put(maximum_packet("STOP", 14)), PutResult.BUSY_TERMINAL)
        self.assertFalse(pending.accept(maximum_packet("STOP", 15)))
        self.assertEqual(mailbox.held_bytes + pending.held_bytes, 49152)
        for remaining, expected in zip(range(10, -1, -1), [*terminals, *ordinary, heartbeat]):
            self.assertIs(mailbox.pop(), expected)
            self.assertEqual(mailbox.held_bytes, remaining * 4096)
        self.assertIsNone(mailbox.pop())
        self.assertEqual(pending.held_bytes, 4096)
        self.assertTrue(pending.finish(pending.peek()))
        self.assertEqual(mailbox.held_bytes + pending.held_bytes, 0)


if __name__ == "__main__":
    unittest.main()
