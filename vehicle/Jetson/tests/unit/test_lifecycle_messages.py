"""초기 lifecycle의 typed 진술/current peer 검사. now·수신 admission·실제 종료 시험은 아니다."""

from dataclasses import FrozenInstanceError, replace
import json
import unittest

from lifecycle_message_fixtures import message_fields, packet, peer
from xenova_vehicle.runtime.local_envelope import decode_structure
from xenova_vehicle.runtime.lifecycle_messages import (
    LifecycleMessage, LifecycleMessageError, LifecyclePeer, parse_lifecycle,
)


_MAX = "18446744073709551615"
_OTHER_UUID = "00000000-0000-4000-8000-000000000099"
_SUPPORTED = ("HELLO", "HEARTBEAT", "STOP", "STOPPED")
_STATE_NAMES = {
    "A": "STARTING ASSET_CHECK DEVICE_BOOT PIPELINE_START WARMUP STREAMING DEGRADED DRAINING CLOSING CLOSED FAILED FAILED_CONFIG FAILED_NEEDS_SERVICE".split(),
    "M": "STARTING WAIT_INPUT WAIT_CLOCK WAIT_ODOM WAIT_STORAGE WARMUP RUNNING DEGRADED QUIESCING RECOVERING FAILED STOPPED".split(),
    "D": "STARTING IDLE ACTIVE_JOB RECOVERING QUIESCING FAILED STOPPED".split(),
}


def structural(value):
    """typed 반례는 먼저 유효한 b1 JSON으로 만든다. 제품 typed encoder를 쓰지 않는다."""
    return decode_structure(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":"), allow_nan=False).encode("utf-8"))


class LifecycleTypedMessageTests(unittest.TestCase):
    def assert_rejected(self, value, current=None, receiver_role="S"):
        current = peer() if current is None else current
        source = structural(value)
        for entry in (parse_lifecycle, LifecycleMessage):
            with self.subTest(entry=entry.__name__), self.assertRaises(LifecycleMessageError):
                entry(source, current, receiver_role)

    def test_all_four_kinds_for_each_registered_role_are_typed_statements_only(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            for kind in _SUPPORTED:
                with self.subTest(role=role, kind=kind):
                    original = packet(kind, current=current)
                    receiver = role if kind == "STOP" else "S"
                    message = parse_lifecycle(original, current, receiver)
                    self.assertEqual(message.fields, message_fields(kind, current=current))
                    self.assertEqual((message.kind, message.sequence, message.issued_us, message.expires_us),
                                     (kind, 1, 100, 200))
                    self.assertIs(message.packet, original)
                    self.assertIs(message.peer, current)
                    self.assertFalse(any(hasattr(message, name) for name in (
                        "authorized", "nn_ready", "metric_ready", "actual_exit", "leases_freed")))

    def test_direct_constructor_revalidates_same_profile_without_admission_state(self):
        current = peer()
        # HELLO·pending STOP·now 없이 진술의 형식만 해석할 수 있다.
        message = LifecycleMessage(packet("STOPPED", current=current), current, "S")
        self.assertEqual(message.kind, "STOPPED")
        bad = message_fields("STOPPED", current=current)
        bad["payload"]["leases_released"] = False
        self.assert_rejected(bad, current)
        for source, expected_peer in ((b"raw-is-not-a-packet", current), (packet("HELLO"), object())):
            for entry in (parse_lifecycle, LifecycleMessage):
                with self.assertRaises(LifecycleMessageError):
                    entry(source, expected_peer, "S")

    def test_registered_peer_fields_and_exact_child_role_are_validated(self):
        original = peer()
        for field in ("service_instance", "channel_epoch", "worker_instance"):
            for bad in (None, True, 1, "00000000000040008000000000000001",
                        "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", "not-a-uuid"):
                with self.subTest(field=field, bad=bad), self.assertRaises(LifecycleMessageError):
                    replace(original, **{field: bad})
        for role in ("S", "a", "UNKNOWN", True, None, 1):
            with self.subTest(role=role), self.assertRaises(LifecycleMessageError):
                replace(original, role=role)
        for release in ("A" * 64, "g" * 64, "a" * 63, "a" * 65, None, 1):
            with self.subTest(release=release), self.assertRaises(LifecycleMessageError):
                replace(original, release_sha256=release)

    def test_only_declared_direction_and_registered_receiver_role_are_supported(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            for kind in _SUPPORTED:
                value = message_fields(kind, current=current)
                wrong_receiver = "S" if kind == "STOP" else role
                self.assert_rejected(value, current, wrong_receiver)
            for receiver in ("unknown", "s", None, True, "D" if role != "D" else "M"):
                self.assert_rejected(message_fields("STOP", current=current), current, receiver)

    def test_other_six_structural_kinds_are_explicitly_unsupported(self):
        for kind in ("BIND", "BIND_ACK", "PROGRESS", "SLOT", "DB_JOB", "FAULT"):
            with self.subTest(kind=kind):
                self.assert_rejected(message_fields(kind))

    def test_lifecycle_domain_and_revision_zero_are_not_raw_binding_admission(self):
        for kind in _SUPPORTED:
            receiver = "A" if kind == "STOP" else "S"
            for domain in ("RAW", "PUBLICATION", "STORAGE"):
                bad = message_fields(kind)
                bad["binding_domain"] = domain
                with self.subTest(kind=kind, domain=domain):
                    self.assert_rejected(bad, receiver_role=receiver)
            bad = message_fields(kind)
            bad["binding_revision"] = "1"
            self.assert_rejected(bad, receiver_role=receiver)

    def test_every_kind_checks_current_service_channel_and_directional_sender(self):
        for kind in _SUPPORTED:
            receiver = "A" if kind == "STOP" else "S"
            for field in ("service_instance", "channel_epoch", "sender_instance"):
                bad = message_fields(kind)
                bad[field] = _OTHER_UUID
                with self.subTest(kind=kind, field=field):
                    self.assert_rejected(bad, receiver_role=receiver)
        wrong = message_fields("STOP")
        wrong["sender_instance"] = peer().worker_instance
        self.assert_rejected(wrong, receiver_role="A")
        wrong = message_fields("HELLO")
        wrong["sender_instance"] = peer().service_instance
        self.assert_rejected(wrong)

    def test_missing_and_unknown_payload_keys_for_every_typed_family(self):
        for kind in _SUPPORTED:
            receiver = "A" if kind == "STOP" else "S"
            for key in message_fields(kind)["payload"]:
                value = message_fields(kind)
                del value["payload"][key]
                with self.subTest(kind=kind, missing=key):
                    self.assert_rejected(value, receiver_role=receiver)
            value = message_fields(kind)
            value["payload"]["unknown"] = None
            self.assert_rejected(value, receiver_role=receiver)

    def test_hello_exact_role_worker_release_and_capability_zero(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            wrongs = (("role", "S"), ("role", role.lower()), ("role", None), ("role", True),
                      ("worker_instance", _OTHER_UUID), ("worker_instance", None),
                      ("release_sha256", "b" * 64), ("release_sha256", "A" * 64),
                      ("release_sha256", "a" * 63), ("release_sha256", None))
            for key, wrong in wrongs:
                value = message_fields("HELLO", current=current)
                value["payload"][key] = wrong
                with self.subTest(role=role, key=key, wrong=wrong):
                    self.assert_rejected(value, current)
            for capability in ("1", _MAX, "00", "-1", "0.0", 0, True, None):
                value = message_fields("HELLO", current=current)
                value["payload"]["capability_mask"] = capability
                self.assert_rejected(value, current)

    def test_heartbeat_every_role_state_is_only_a_producer_claim(self):
        for role, states in _STATE_NAMES.items():
            current = peer(role)
            for state in states:
                with self.subTest(role=role, state=state):
                    source = packet("HEARTBEAT", current=current, payload={"worker_state": state,
                                    "last_progress_sequence": None, "last_progress_j_us": None})
                    result = parse_lifecycle(source, current, "S")
                    self.assertEqual(result.payload["worker_state"], state)
                    self.assertFalse(hasattr(result, "process_exited_us"))
        for role, wrong in (("A", "RUNNING"), ("M", "CLOSED"), ("D", "WAIT_STORAGE")):
            value = message_fields("HEARTBEAT", current=peer(role))
            value["payload"]["worker_state"] = wrong
            self.assert_rejected(value, peer(role))
        for wrong in ("starting", "UNKNOWN", None, True, 1):
            value = message_fields("HEARTBEAT")
            value["payload"]["worker_state"] = wrong
            self.assert_rejected(value)

    def test_heartbeat_null_tuple_zero_and_equal_issue_time_boundaries(self):
        current = peer()
        for sequence, completed, issued in ((None, None, 100), ("0", "0", 0),
                                           ("1", "100", 100), (_MAX, _MAX, 2**64 - 1)):
            value = message_fields("HEARTBEAT", issued=issued, expires=2**64 - 1)
            value["payload"].update(last_progress_sequence=sequence, last_progress_j_us=completed)
            result = parse_lifecycle(structural(value), current, "S")
            self.assertEqual(result.payload, value["payload"])
        for sequence, completed in ((None, "0"), ("0", None), ("1", "101")):
            value = message_fields("HEARTBEAT")
            value["payload"].update(last_progress_sequence=sequence, last_progress_j_us=completed)
            self.assert_rejected(value)

    def test_heartbeat_u64_reports_cannot_be_numeric_boolean_or_noncanonical(self):
        for field in ("last_progress_sequence", "last_progress_j_us"):
            for wrong in ("", "01", "-1", "+1", " 1", "1.0", "18446744073709551616", "١", 0, 1, True, 1.0):
                value = message_fields("HEARTBEAT")
                value["payload"].update(last_progress_sequence="1", last_progress_j_us="100")
                value["payload"][field] = wrong
                with self.subTest(field=field, wrong=wrong):
                    self.assert_rejected(value)

    def test_stop_revision_positive_u64_for_stop_and_stopped(self):
        for kind in ("STOP", "STOPPED"):
            receiver = "A" if kind == "STOP" else "S"
            for revision in ("1", _MAX):
                value = message_fields(kind)
                value["payload"]["stop_revision"] = revision
                self.assertEqual(parse_lifecycle(structural(value), peer(), receiver).payload["stop_revision"], revision)
            for wrong in ("0", "", "01", "-1", "1.0", "18446744073709551616", 1, True, None):
                value = message_fields(kind)
                value["payload"]["stop_revision"] = wrong
                with self.subTest(kind=kind, wrong=wrong):
                    self.assert_rejected(value, receiver_role=receiver)

    def test_stop_drain_inside_original_issue_and_expiry_is_typed_not_now_check(self):
        current = peer()
        for issued, drain, expires in ((0, 0, 0), (100, 100, 200), (100, 200, 200),
                                       (2**64 - 1, 2**64 - 1, 2**64 - 1)):
            value = message_fields("STOP", issued=issued, drain=drain, expires=expires)
            result = parse_lifecycle(structural(value), current, "A")
            self.assertEqual(result.payload["drain_deadline_j_us"], str(drain))
        for drain in ("99", "201", "-1", "01", "18446744073709551616", 100, True, None):
            value = message_fields("STOP")
            value["payload"]["drain_deadline_j_us"] = drain
            self.assert_rejected(value, receiver_role="A")

    def test_stop_reason_printable_ascii1_to32_and_diagnostic_spaces_are_allowed(self):
        for reason in ("x", " " * 32, "!" * 32, "~", 'diagnostic "quote" \\ only'):
            value = message_fields("STOP")
            value["payload"]["reason"] = reason
            result = parse_lifecycle(structural(value), peer(), "A")
            self.assertEqual(result.payload["reason"], reason)
        for wrong in ("", "x" * 33, "\n", "\r", "\t", "\x00", "\x1f", "\x7f", "중단", None, True, 1):
            value = message_fields("STOP")
            value["payload"]["reason"] = wrong
            self.assert_rejected(value, receiver_role="A")

    def test_stopped_success_requires_role_specific_statements_but_is_not_actual_exit(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            result = parse_lifecycle(packet("STOPPED", current=current), current, "S")
            self.assertTrue(result.payload["leases_released"])
            self.assertEqual(result.payload["device_closed"], True if role == "A" else None)
            self.assertIsNone(result.payload["checkpoint_job"])
            self.assertFalse(hasattr(result, "session_exit_evidence"))
            value = message_fields("STOPPED", current=current)
            value["payload"]["leases_released"] = False
            self.assert_rejected(value, current)
        value = message_fields("STOPPED")
        value["payload"]["device_closed"] = False
        self.assert_rejected(value)

    def test_failed_partial_statements_are_preserved_without_requiring_success(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            for released in (False, True):
                for closed in ((False, True) if role == "A" else (None,)):
                    with self.subTest(role=role, released=released, closed=closed):
                        value = message_fields("STOPPED", current=current)
                        value["payload"].update(status="FAILED", leases_released=released, device_closed=closed)
                        result = parse_lifecycle(structural(value), current, "S")
                        self.assertEqual(result.payload, value["payload"])

    def test_stopped_bool_null_status_and_deferred_checkpoint_uuid_are_exact(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            wrongs = [("leases_released", value) for value in (0, 1, "true", None)]
            wrongs += [("checkpoint_job", value) for value in (_OTHER_UUID, "", 0, False, {})]
            wrongs += [("status", value) for value in ("CLOSED", "STOPPED ", "failed", None, True)]
            wrongs += [("device_closed", value) for value in ((None, 0, 1, "true") if role == "A" else (False, True, 0, "null"))]
            for key, wrong in wrongs:
                value = message_fields("STOPPED", current=current)
                value["payload"].update(status="FAILED")
                value["payload"][key] = wrong
                with self.subTest(role=role, key=key, wrong=wrong):
                    self.assert_rejected(value, current)

    def test_sequence_and_original_time_properties_preserve_u64_values(self):
        for sequence, issued, expires in ((1, 0, 1), (2**64 - 1, 2**64 - 2, 2**64 - 1)):
            current = peer()
            original = packet("HELLO", current=current, sequence=sequence, issued=issued, expires=expires)
            result = parse_lifecycle(original, current, "S")
            self.assertEqual((result.sequence, result.issued_us, result.expires_us), (sequence, issued, expires))
            self.assertIs(type(result.sequence), int)
            self.assertEqual(result.fields["message_sequence"], str(sequence))
            self.assertEqual(result.packet.raw_bytes, original.raw_bytes)

    def test_typed_copies_and_frozen_peer_cannot_change_original_statement(self):
        current = peer()
        value = message_fields("STOP", current=current)
        source = structural(value)
        result = LifecycleMessage(source, current, "A")
        raw_before = result.packet.raw_bytes
        value["payload"]["reason"] = "changed caller"
        value["issued_j_us"] = "999"
        returned = result.fields
        returned["payload"]["reason"] = "changed fields"
        returned["sender_instance"] = _OTHER_UUID
        result.payload["reason"] = "changed payload"
        self.assertEqual(result.payload["reason"], "operator")
        self.assertEqual(result.issued_us, 100)
        self.assertEqual(result.packet.raw_bytes, raw_before)
        self.assertIsNot(result.fields, result.fields)
        self.assertIsNot(result.payload, result.payload)
        with self.assertRaises(FrozenInstanceError):
            result.receiver_role = "S"
        with self.assertRaises(FrozenInstanceError):
            current.worker_instance = _OTHER_UUID


if __name__ == "__main__":
    unittest.main()
