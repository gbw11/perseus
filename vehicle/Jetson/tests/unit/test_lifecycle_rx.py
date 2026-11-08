"""현재 lifecycle 수신의 합성 사건 시험. 실제 send·peer·close/exit·lease 반환은 검증하지 않는다."""

from dataclasses import FrozenInstanceError, replace
import json
import unittest

from lifecycle_message_fixtures import packet, peer
from xenova_vehicle.runtime.local_envelope import decode_structure
from xenova_vehicle.runtime.lifecycle_messages import (
    LifecycleMessage, LifecycleMessageError, LifecycleReceiver, RxDisposition,
)


MAX_U64 = 2**64 - 1
OTHER = "00000000-0000-4000-8000-000000000099"


def modified(value, *, payload=None, **changes):
    fields = value.fields
    fields.update(changes)
    if payload is not None:
        fields["payload"] = payload
    return decode_structure(json.dumps(fields, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":")).encode("utf-8"))


class ReceiverAssertions(unittest.TestCase):
    def accepted(self, receiver, value, now=140, cap=None):
        result = receiver.receive(value, now_us=now, cap_us=cap)
        self.assertIs(result.disposition, RxDisposition.ACCEPTED_NEW)
        self.assertIs(type(result.message), LifecycleMessage)
        self.assertIs(result.message.packet, value)
        return result.message

    def rejected_unchanged(self, receiver, value, now=140, cap=None):
        before = receiver.snapshot
        with self.assertRaises(LifecycleMessageError):
            receiver.receive(value, now_us=now, cap_us=cap)
        self.assertEqual(receiver.snapshot, before)

    def sent_stop(self, receiver, *, sequence=1, issued=100, expires=200, drain=180,
                  lower=120, registered=131):
        value = packet("STOP", current=receiver.snapshot.peer, sequence=sequence,
                       issued=issued, expires=expires, drain=drain)
        self.assertTrue(receiver.register_stop_intent(value, now_us=issued))
        self.assertTrue(receiver.record_first_sent(value, lower, now_us=registered))
        return value


class ReceiveOrderTests(ReceiverAssertions):
    def test_initial_receive_gap_and_later_gaps_are_valid_for_every_child_role(self):
        for role in ("A", "M", "D"):
            with self.subTest(role=role):
                current = peer(role)
                receiver = LifecycleReceiver(current, "S")
                self.assertEqual(receiver.snapshot.highwater, 0)
                self.assertFalse(receiver.snapshot.hello_seen)
                hello = packet("HELLO", current=current, sequence=7)
                self.accepted(receiver, hello)
                self.assertEqual(receiver.snapshot.highwater, 7)
                self.assertTrue(receiver.snapshot.hello_seen)
                heartbeat = packet("HEARTBEAT", current=current, sequence=11)
                self.accepted(receiver, heartbeat)
                self.assertEqual(receiver.snapshot.highwater, 11)

    def test_invalid_high_sequence_never_poisons_a_later_normal_lower_packet(self):
        receiver = LifecycleReceiver(peer(), "S")
        hello = packet("HELLO", sequence=100, issued=150)
        self.rejected_unchanged(receiver, hello, now=140)
        self.accepted(receiver, packet("HELLO", sequence=4))
        invalid = packet("HEARTBEAT", sequence=100, payload={"worker_state": "RUNNING",
                          "last_progress_sequence": None, "last_progress_j_us": None})
        self.rejected_unchanged(receiver, invalid)
        self.accepted(receiver, packet("HEARTBEAT", sequence=5))
        self.assertEqual(receiver.snapshot.highwater, 5)

    def test_heartbeat_requires_hello_and_rejected_event_does_not_consume_sequence(self):
        receiver = LifecycleReceiver(peer(), "S")
        self.rejected_unchanged(receiver, packet("HEARTBEAT", sequence=50))
        self.accepted(receiver, packet("HELLO", sequence=2))
        self.accepted(receiver, packet("HEARTBEAT", sequence=3))

    def test_last_original_duplicate_has_no_message_or_state_effect(self):
        receiver = LifecycleReceiver(peer(), "S")
        original = packet("HELLO", sequence=3)
        self.accepted(receiver, original)
        before = receiver.snapshot
        duplicate = receiver.receive(original, now_us=150)
        self.assertIs(duplicate.disposition, RxDisposition.DUPLICATE_NO_EFFECT)
        self.assertIsNone(duplicate.message)
        self.assertEqual(receiver.snapshot, before)
        self.rejected_unchanged(receiver, packet("HELLO", sequence=4))
        self.assertEqual(receiver.snapshot.highwater, 3)

    def test_same_sequence_different_original_bytes_is_collision_not_reencoding_duplicate(self):
        receiver = LifecycleReceiver(peer(), "S")
        original = packet("HELLO", sequence=5)
        self.accepted(receiver, original)
        altered_wire = json.dumps(original.fields, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8")
        self.assertNotEqual(altered_wire, original.raw_bytes)
        same_fields = decode_structure(altered_wire)
        self.assertEqual(same_fields.fields, original.fields)
        self.rejected_unchanged(receiver, same_fields)

    def test_old_original_packet_is_not_accepted_as_the_last_duplicate(self):
        receiver = LifecycleReceiver(peer(), "S")
        first = packet("HELLO", sequence=3)
        self.accepted(receiver, first)
        self.accepted(receiver, packet("HEARTBEAT", sequence=4))
        self.rejected_unchanged(receiver, first)
        self.assertEqual(receiver.snapshot.highwater, 4)

    def test_current_peer_type_lifetime_and_cap_are_checked_before_duplicate(self):
        receiver = LifecycleReceiver(peer(), "S")
        first = packet("HELLO", sequence=5)
        self.accepted(receiver, first)
        for field in ("service_instance", "channel_epoch", "sender_instance"):
            with self.subTest(field=field):
                self.rejected_unchanged(receiver, modified(first, **{field: OTHER}))
        bad_payload = first.payload
        bad_payload["capability_mask"] = "1"
        self.rejected_unchanged(receiver, modified(first, payload=bad_payload))
        self.rejected_unchanged(receiver, first, now=99)
        self.rejected_unchanged(receiver, first, now=200)
        self.rejected_unchanged(receiver, first, now=140, cap=199)
        duplicate = receiver.receive(first, now_us=140, cap_us=200)
        self.assertIs(duplicate.disposition, RxDisposition.DUPLICATE_NO_EFFECT)

    def test_unsupported_kind_or_wrong_direction_does_not_advance_state(self):
        receiver = LifecycleReceiver(peer(), "S")
        for kind in ("BIND", "BIND_ACK", "PROGRESS", "SLOT", "DB_JOB", "FAULT", "STOP"):
            with self.subTest(kind=kind):
                self.rejected_unchanged(receiver, packet(kind, sequence=99))
        self.accepted(receiver, packet("HELLO", sequence=1))

    def test_raw_or_wrong_packet_types_are_errors_without_partial_admission(self):
        receiver = LifecycleReceiver(peer(), "S")
        for value in (None, {}, b"{}", True):
            with self.subTest(type=type(value).__name__):
                self.rejected_unchanged(receiver, value)
        self.accepted(receiver, packet("HELLO"))

    def test_maximum_sequence_never_wraps_and_new_channel_needs_new_registration(self):
        current = peer()
        receiver = LifecycleReceiver(current, "S")
        original = packet("HELLO", sequence=MAX_U64)
        self.accepted(receiver, original)
        self.assertEqual(receiver.snapshot.highwater, MAX_U64)
        self.rejected_unchanged(receiver, packet("HEARTBEAT", sequence=1))
        self.rejected_unchanged(receiver, packet("HEARTBEAT", sequence=MAX_U64))
        replacement = replace(current, channel_epoch=OTHER)
        new_first = packet("HELLO", current=replacement, sequence=1)
        self.rejected_unchanged(receiver, new_first)
        new_receiver = LifecycleReceiver(replacement, "S")
        self.accepted(new_receiver, new_first)
        self.assertEqual(new_receiver.snapshot.highwater, 1)


class ReceiveLifetimeTests(ReceiverAssertions):
    def test_issue_and_expiry_half_open_boundaries_are_not_retimed(self):
        for now, expected in ((99, False), (100, True), (199, True), (200, False), (201, False)):
            with self.subTest(now=now):
                receiver = LifecycleReceiver(peer(), "S")
                value = packet("HELLO")
                if expected:
                    message = self.accepted(receiver, value, now)
                    self.assertEqual((message.issued_us, message.expires_us), (100, 200))
                else:
                    self.rejected_unchanged(receiver, value, now)

    def test_exact_registered_cap_and_full_u64_time_range(self):
        for cap, expected in ((199, False), (200, True), (201, True)):
            with self.subTest(cap=cap):
                receiver = LifecycleReceiver(peer(), "S")
                if expected:
                    self.accepted(receiver, packet("HELLO"), cap=cap)
                else:
                    self.rejected_unchanged(receiver, packet("HELLO"), cap=cap)
        receiver = LifecycleReceiver(peer(), "S")
        value = packet("HELLO", issued=MAX_U64 - 1, expires=MAX_U64)
        self.accepted(receiver, value, MAX_U64 - 1)
        self.rejected_unchanged(receiver, value, MAX_U64)

    def test_now_and_optional_cap_require_exact_u64_values(self):
        receiver = LifecycleReceiver(peer(), "S")
        for invalid in (True, 140.0, -1, MAX_U64 + 1, "140", None):
            with self.subTest(now=invalid):
                self.rejected_unchanged(receiver, packet("HELLO"), now=invalid)
            if invalid is not None:
                with self.subTest(cap=invalid):
                    self.rejected_unchanged(receiver, packet("HELLO"), cap=invalid)
        self.accepted(receiver, packet("HELLO"), cap=None)

    def test_child_accepts_prehello_stop_without_allocating_a_new_drain_budget(self):
        for role in ("A", "M", "D"):
            for now in (179, 180, 181):
                with self.subTest(role=role, now=now):
                    current = peer(role)
                    receiver = LifecycleReceiver(current, role)
                    value = packet("STOP", current=current, sequence=7)
                    message = self.accepted(receiver, value, now)
                    self.assertEqual(message.payload["drain_deadline_j_us"], "180")
                    self.assertEqual(message.expires_us, 200)
                    self.assertFalse(receiver.snapshot.hello_seen)
                    self.assertEqual(receiver.snapshot.highwater, 7)
                    self.assertEqual(receiver.snapshot.pending_stop.message.packet.raw_bytes, value.raw_bytes)

    def test_worker_last_stop_duplicate_is_effectless_before_the_active_stop_gate(self):
        receiver = LifecycleReceiver(peer(), "A")
        stop = packet("STOP", sequence=7)
        self.accepted(receiver, stop, now=100)
        before = receiver.snapshot
        duplicate = receiver.receive(stop, now_us=180)
        self.assertIs(duplicate.disposition, RxDisposition.DUPLICATE_NO_EFFECT)
        self.assertIsNone(duplicate.message)
        self.assertEqual(receiver.snapshot, before)
        self.rejected_unchanged(receiver, packet("STOP", sequence=8), now=180)


class StopIntentAndReportTests(ReceiverAssertions):
    def test_intent_registration_and_first_send_fact_are_not_rx_or_actual_send_effects(self):
        receiver = LifecycleReceiver(peer(), "S")
        stop = packet("STOP", sequence=77)
        self.assertTrue(receiver.register_stop_intent(stop, now_us=100))
        snapshot = receiver.snapshot
        self.assertEqual(snapshot.highwater, 0)
        self.assertFalse(snapshot.hello_seen)
        self.assertIsNone(snapshot.last_packet)
        self.assertIsNone(snapshot.stopped_report)
        self.assertIsNone(snapshot.pending_stop.first_sent_us)
        self.assertEqual(snapshot.pending_stop.message.packet.raw_bytes, stop.raw_bytes)
        self.assertTrue(receiver.record_first_sent(stop, 120, now_us=131))
        self.assertEqual(receiver.snapshot.pending_stop.first_sent_us, 120)
        self.assertEqual(receiver.snapshot.highwater, 0)
        self.assertFalse(receiver.snapshot.hello_seen)
        self.rejected_unchanged(receiver, packet("HELLO", sequence=1), now=140)
        self.assertEqual(receiver.snapshot.highwater, 0)

    def test_send_lower120_report125_registration131_receive140_is_valid_before_hello(self):
        for role in ("A", "M", "D"):
            with self.subTest(role=role):
                current = peer(role)
                receiver = LifecycleReceiver(current, "S")
                stop = self.sent_stop(receiver, lower=120, registered=131)
                report = packet("STOPPED", current=current, issued=125, expires=200)
                accepted = self.accepted(receiver, report, now=140)
                snapshot = receiver.snapshot
                self.assertFalse(snapshot.hello_seen)
                self.assertEqual(snapshot.highwater, 1)
                self.assertEqual(snapshot.pending_stop.message.packet.raw_bytes, stop.raw_bytes)
                self.assertEqual(snapshot.pending_stop.first_sent_us, 120)
                self.assertEqual(snapshot.stopped_report.packet.raw_bytes, report.raw_bytes)
                self.assertIs(type(accepted), LifecycleMessage)  # producer 진술이며 exit/lease fact가 아니다.

    def test_stopped_without_intent_or_first_send_is_rejected_without_losing_its_sequence(self):
        receiver = LifecycleReceiver(peer(), "S")
        report = packet("STOPPED", issued=125)
        self.rejected_unchanged(receiver, report)
        stop = packet("STOP")
        receiver.register_stop_intent(stop, now_us=100)
        self.rejected_unchanged(receiver, report)
        receiver.record_first_sent(stop, 120, now_us=131)
        self.accepted(receiver, report)

    def test_stopped_requires_original_request_revision_send_lower_and_original_expiry(self):
        receiver = LifecycleReceiver(peer(), "S")
        self.sent_stop(receiver)
        for bad in (packet("STOPPED", issued=125, request_id=OTHER),
                    packet("STOPPED", issued=125, revision=2),
                    packet("STOPPED", issued=119), packet("STOPPED", issued=125, expires=201),
                    packet("STOPPED", issued=250, expires=1000)):
            with self.subTest(fields=bad.fields):
                self.rejected_unchanged(receiver, bad, now=300 if bad.fields["issued_j_us"] == "250" else 140)
        self.accepted(receiver, packet("STOPPED", issued=120, expires=200), now=140)

    def test_report_is_one_statement_per_worker_lifetime_with_effectless_last_duplicate(self):
        receiver = LifecycleReceiver(peer(), "S")
        self.sent_stop(receiver)
        report = packet("STOPPED", sequence=2, issued=125)
        self.accepted(receiver, report)
        before = receiver.snapshot
        result = receiver.receive(report, now_us=150)
        self.assertIs(result.disposition, RxDisposition.DUPLICATE_NO_EFFECT)
        self.assertIsNone(result.message)
        self.assertEqual(receiver.snapshot, before)
        self.rejected_unchanged(receiver, packet("STOPPED", sequence=3, issued=125))
        self.rejected_unchanged(receiver, report, now=200)

    def test_active_stop_never_accepts_new_key_new_revision_or_new_sequence_renewal(self):
        for receiver_role in ("S", "A"):
            with self.subTest(receiver_role=receiver_role):
                receiver = LifecycleReceiver(peer(), receiver_role)
                original = packet("STOP")
                if receiver_role == "S":
                    receiver.register_stop_intent(original, now_us=100)
                else:
                    self.accepted(receiver, original, now=100)
                before = receiver.snapshot
                attempts = (packet("STOP", sequence=2, issued=110, expires=300, drain=250),
                            packet("STOP", sequence=2, request_id=OTHER), packet("STOP", sequence=2, revision=2))
                for value in attempts:
                    with self.subTest(fields=value.fields), self.assertRaises(LifecycleMessageError):
                        if receiver_role == "S":
                            receiver.register_stop_intent(value, now_us=140)
                        else:
                            receiver.receive(value, now_us=140)
                    self.assertEqual(receiver.snapshot, before)

    def test_intent_exact_duplicate_is_effectless_but_other_raw_bytes_conflict(self):
        receiver = LifecycleReceiver(peer(), "S")
        original = packet("STOP")
        self.assertTrue(receiver.register_stop_intent(original, now_us=100))
        before = receiver.snapshot
        self.assertFalse(receiver.register_stop_intent(original, now_us=150))
        self.assertEqual(receiver.snapshot, before)
        altered = decode_structure(json.dumps(original.fields, separators=(", ", ": ")).encode("utf-8"))
        with self.assertRaises(LifecycleMessageError):
            receiver.register_stop_intent(altered, now_us=150)
        self.assertEqual(receiver.snapshot, before)

    def test_intent_registered_cap_is_checked_before_any_stop_state_is_created(self):
        receiver = LifecycleReceiver(peer(), "S")
        stop = packet("STOP")
        before = receiver.snapshot
        with self.assertRaises(LifecycleMessageError):
            receiver.register_stop_intent(stop, now_us=100, cap_us=199)
        self.assertEqual(receiver.snapshot, before)
        self.assertTrue(receiver.register_stop_intent(stop, now_us=100, cap_us=200))
        self.assertEqual(receiver.snapshot.highwater, 0)
        with self.assertRaises(LifecycleMessageError):
            receiver.register_stop_intent(stop, now_us=200, cap_us=200)

    def test_completed_report_does_not_allow_a_new_stop_lifetime_on_the_same_peer(self):
        receiver = LifecycleReceiver(peer(), "S")
        self.sent_stop(receiver)
        self.accepted(receiver, packet("STOPPED", issued=125))
        before = receiver.snapshot
        another = packet("STOP", sequence=2, request_id=OTHER, revision=2, issued=150, expires=300, drain=250)
        with self.assertRaises(LifecycleMessageError):
            receiver.register_stop_intent(another, now_us=150)
        self.assertEqual(receiver.snapshot, before)

    def test_first_send_exact_duplicate_is_effectless_but_different_fact_conflicts(self):
        receiver = LifecycleReceiver(peer(), "S")
        original = self.sent_stop(receiver)
        before = receiver.snapshot
        self.assertFalse(receiver.record_first_sent(original, 120, now_us=150))
        self.assertEqual(receiver.snapshot, before)
        for changed, lower in ((original, 121), (packet("STOP", request_id=OTHER), 120)):
            with self.subTest(lower=lower), self.assertRaises(LifecycleMessageError):
                receiver.record_first_sent(changed, lower, now_us=150)
            self.assertEqual(receiver.snapshot, before)

    def test_expired_exact_intent_and_send_fact_duplicates_do_not_bypass_lifetime(self):
        receiver = LifecycleReceiver(peer(), "S")
        stop = self.sent_stop(receiver)
        before = receiver.snapshot
        self.assertFalse(receiver.register_stop_intent(stop, now_us=199))
        self.assertFalse(receiver.record_first_sent(stop, 120, now_us=199))
        for now in (200, 201):
            with self.subTest(now=now):
                with self.assertRaises(LifecycleMessageError):
                    receiver.register_stop_intent(stop, now_us=now)
                self.assertEqual(receiver.snapshot, before)
                with self.assertRaises(LifecycleMessageError):
                    receiver.record_first_sent(stop, 120, now_us=now)
                self.assertEqual(receiver.snapshot, before)

    def test_new_intent_or_first_sent_registration_must_be_inside_original_lifetime(self):
        for now in (99, 200, 201):
            with self.subTest(intent_now=now):
                receiver = LifecycleReceiver(peer(), "S")
                before = receiver.snapshot
                with self.assertRaises(LifecycleMessageError):
                    receiver.register_stop_intent(packet("STOP"), now_us=now)
                self.assertEqual(receiver.snapshot, before)
        for lower, now in ((99, 131), (132, 131), (120, 200), (120, 201)):
            with self.subTest(lower=lower, first_sent_now=now):
                receiver = LifecycleReceiver(peer(), "S")
                original = packet("STOP")
                receiver.register_stop_intent(original, now_us=100)
                before = receiver.snapshot
                with self.assertRaises(LifecycleMessageError):
                    receiver.record_first_sent(original, lower, now_us=now)
                self.assertEqual(receiver.snapshot, before)

    def test_unregistered_or_wrong_direction_send_fact_cannot_create_stop_state(self):
        receiver = LifecycleReceiver(peer(), "S")
        before = receiver.snapshot
        with self.assertRaises(LifecycleMessageError):
            receiver.record_first_sent(packet("STOP"), 120, now_us=131)
        self.assertEqual(receiver.snapshot, before)
        child = LifecycleReceiver(peer(), "A")
        child_before = child.snapshot
        with self.assertRaises(LifecycleMessageError):
            child.register_stop_intent(packet("STOP"), now_us=100)
        self.assertEqual(child.snapshot, child_before)


class SnapshotOwnershipTests(ReceiverAssertions):
    def test_public_snapshot_and_json_copies_cannot_change_the_accepted_state(self):
        current = peer()
        receiver = LifecycleReceiver(current, "S")
        stop = self.sent_stop(receiver)
        report = packet("STOPPED", issued=125)
        message = self.accepted(receiver, report)
        before = receiver.snapshot
        for target, field, value in ((before, "highwater", 999), (before.peer, "worker_instance", OTHER),
                                     (before.pending_stop, "first_sent_us", 999)):
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(target, field, value)
        message.fields["expires_j_us"] = "999999"
        message.payload["status"] = "FAILED"
        before.pending_stop.message.fields["payload"]["drain_deadline_j_us"] = "999999"
        before.pending_stop.message.payload["reason"] = "modified"
        before.stopped_report.payload["leases_released"] = False
        before.last_packet.fields["kind"] = "HELLO"
        self.assertEqual(receiver.snapshot, before)
        self.assertEqual(receiver.snapshot.pending_stop.message.packet.raw_bytes, stop.raw_bytes)
        self.assertEqual(receiver.snapshot.pending_stop.message.payload["drain_deadline_j_us"], "180")
        self.assertEqual(receiver.snapshot.stopped_report.payload["status"], "STOPPED")
        self.assertEqual(receiver.snapshot.stopped_report.payload["leases_released"], True)
        self.assertEqual(message.expires_us, 200)


if __name__ == "__main__":
    unittest.main()
