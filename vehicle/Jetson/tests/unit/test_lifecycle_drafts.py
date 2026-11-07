"""순번 없는 lifecycle 진술의 형식·소유권 시험. 송신/수신 수락·실제 정리 권한은 시험하지 않는다."""

from contextlib import ExitStack
from dataclasses import FrozenInstanceError, replace
import unittest
from unittest.mock import call, patch

from lifecycle_draft_fixtures import draft, request
from lifecycle_message_fixtures import peer
from xenova_vehicle.runtime import lifecycle_messages, tx_outbox
from xenova_vehicle.runtime.tx_outbox import LifecycleDraft, TxOutboxError


_MAX = 2**64 - 1
_KINDS = ("HELLO", "HEARTBEAT", "STOP", "STOPPED")
_STATES = {
    "A": "STARTING ASSET_CHECK DEVICE_BOOT PIPELINE_START WARMUP STREAMING DEGRADED DRAINING CLOSING CLOSED FAILED FAILED_CONFIG FAILED_NEEDS_SERVICE".split(),
    "M": "STARTING WAIT_INPUT WAIT_CLOCK WAIT_ODOM WAIT_STORAGE WARMUP RUNNING DEGRADED QUIESCING RECOVERING FAILED STOPPED".split(),
    "D": "STARTING IDLE ACTIVE_JOB RECOVERING QUIESCING FAILED STOPPED".split(),
}
_FIELDS = frozenset(("version", "service_instance", "channel_epoch", "sender_instance",
                     "kind", "binding_domain", "binding_revision", "request_id",
                     "issued_j_us", "expires_j_us", "payload"))


def _payload(kind, current):
    """기대 schema를 순수 literal로 만든다. 제품 serializer/validator를 경유하지 않는다."""
    if kind == "HELLO":
        return dict(role=current.role, worker_instance=current.worker_instance,
                    release_sha256=current.release_sha256, capability_mask="0")
    if kind == "HEARTBEAT":
        return dict(worker_state="STARTING", last_progress_sequence=None,
                    last_progress_j_us=None)
    if kind == "STOP":
        return dict(stop_revision="1", reason="operator", drain_deadline_j_us="180")
    return dict(stop_revision="1", leases_released=True,
                device_closed=True if current.role == "A" else None,
                checkpoint_job=None, status="STOPPED")


def _arguments(kind="HELLO", current=None, **updates):
    current = peer() if current is None else current
    result = dict(peer=current, receiver_role=current.role if kind == "STOP" else "S",
                  kind=kind, request_id=request(4), issued_us=100, expires_us=200,
                  payload=_payload(kind, current), cap_us=None)
    result.update(updates)
    return result


class LifecycleDraftTests(unittest.TestCase):
    def assert_rejected(self, arguments):
        with self.assertRaises(TxOutboxError):
            LifecycleDraft(**arguments)

    def test_four_kinds_for_three_roles_have_real_eleven_field_statements_only(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            for kind in _KINDS:
                with self.subTest(role=role, kind=kind):
                    value = draft(kind, current=current, cap_us=200)
                    expected = dict(version=1, service_instance=current.service_instance,
                                    channel_epoch=current.channel_epoch,
                                    sender_instance=current.service_instance if kind == "STOP" else current.worker_instance,
                                    kind=kind, binding_domain="LIFECYCLE", binding_revision="0",
                                    request_id=request(4), issued_j_us="100", expires_j_us="200",
                                    payload=_payload(kind, current))
                    self.assertEqual(value.fields, expected)
                    self.assertEqual(set(value.fields), _FIELDS)
                    self.assertIs(value.peer, current)
                    self.assertEqual((value.issued_us, value.expires_us, value.cap_us), (100, 200, 200))
                    self.assertFalse(any(hasattr(value, name) for name in (
                        "sequence", "message_sequence", "raw_bytes", "packet", "authorized",
                        "nn_ready", "metric_ready", "actual_exit", "leases_freed")))

    def test_public_constructor_and_fixture_need_no_encoder_fake_uuid_or_clock(self):
        current = peer()
        arguments = [_arguments(kind, current) for kind in _KINDS]
        # UUID(request) 형식 확인은 필요하지만 새 UUID/packet/순번 발급은 필요하지 않다.
        with ExitStack() as stack:
            uuid_check = stack.enter_context(patch.object(lifecycle_messages, "UUID", wraps=lifecycle_messages.UUID))
            for target in ("xenova_vehicle.runtime.tx_outbox.encode_structure",
                           "xenova_vehicle.runtime.local_envelope.encode_structure",
                           "xenova_vehicle.runtime.local_envelope.StructuralPacket",
                           "xenova_vehicle.runtime.tx_outbox.PendingTx", "uuid.uuid1", "uuid.uuid4",
                           "time.time", "time.monotonic", "time.monotonic_ns",
                           "copy.deepcopy", "xenova_vehicle.runtime.lifecycle_messages.deepcopy"):
                stack.enter_context(patch(target, side_effect=AssertionError("draft must not call " + target)))
            values = [LifecycleDraft(**item) for item in arguments]
            snapshots = [(value.fields, value.payload, value.fingerprint) for value in values]
        self.assertEqual(uuid_check.call_args_list, [call(request(4)), call(current.worker_instance),
                                                    call(request(4)), call(request(4)), call(request(4))])
        # HELLO의 실제 worker UUID 1회와 네 request UUID만 검사한다. nil/probe ID는 없다.
        self.assertTrue(all(set(fields) == _FIELDS for fields, _, _ in snapshots))

    def test_constructor_cannot_accept_sequence_header_or_prevalidated_private_state(self):
        for name, value in (("message_sequence", "1"), ("sequence", 1), ("sender_instance", request(99)),
                            ("_payload", {}), ("_validated", True), ("_fields", {}), ("packet", object())):
            arguments = _arguments()
            arguments[name] = value
            with self.subTest(name=name), self.assertRaises(TypeError):
                LifecycleDraft(**arguments)
        self.assert_rejected(_arguments(payload={"role": "A"}))

    def test_exact_peer_kind_role_and_declared_direction_are_required(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            for kind in _KINDS:
                wrong = "S" if kind == "STOP" else role
                self.assert_rejected(_arguments(kind, current, receiver_role=wrong))
            for wrong in (None, True, 1, "", "s", "SS", "M" if role != "M" else "D"):
                self.assert_rejected(_arguments("STOP", current, receiver_role=wrong))
        for wrong in (None, {}, object()):
            self.assert_rejected(_arguments(peer=wrong))
        for kind in ("BIND", "BIND_ACK", "PROGRESS", "SLOT", "DB_JOB", "FAULT", "", "hello", None, True, 1):
            self.assert_rejected(_arguments(kind=kind))

    def test_request_is_caller_supplied_canonical_uuid_not_generated_or_repaired(self):
        for wrong in (None, True, 1, "", "x" * 36, request(4).replace("-", ""),
                      "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", " " + request(4), request(4) + " "):
            self.assert_rejected(_arguments(request_id=wrong))
        original = request(123)
        value = LifecycleDraft(**_arguments(request_id=original))
        self.assertEqual(value.request_id, original)
        self.assertEqual(value.fields["request_id"], original)

    def test_peer_and_integer_subclasses_do_not_bypass_exact_constructor_guards(self):
        current = peer()

        class PeerSubclass(type(current)):
            pass

        class IntegerSubclass(int):
            def __lt__(self, other):
                raise AssertionError("native subclass comparison must not run")

            def __le__(self, other):
                raise AssertionError("native subclass comparison must not run")

            def __ge__(self, other):
                raise AssertionError("native subclass comparison must not run")

        inherited = PeerSubclass(current.service_instance, current.channel_epoch,
                                 current.worker_instance, current.role, current.release_sha256)
        self.assert_rejected(_arguments(peer=inherited))
        for name, number in (("issued_us", 100), ("expires_us", 200), ("cap_us", 200)):
            self.assert_rejected(_arguments(**{name: IntegerSubclass(number)}))

    def test_time_inputs_are_exact_u64_and_cap_does_not_rewrite_original_window(self):
        for issued, expires, cap in ((0, 1, None), (0, 1, 1), (100, 200, 201),
                                     (_MAX - 1, _MAX, _MAX)):
            value = LifecycleDraft(**_arguments(issued_us=issued, expires_us=expires, cap_us=cap))
            self.assertEqual((value.issued_us, value.expires_us, value.cap_us), (issued, expires, cap))
            self.assertEqual((value.fields["issued_j_us"], value.fields["expires_j_us"]),
                             (str(issued), str(expires)))
        for name in ("issued_us", "expires_us", "cap_us"):
            for wrong in (False, True, -1, _MAX + 1, 1.0, "100", object()):
                with self.subTest(name=name, wrong_type=type(wrong).__name__):
                    self.assert_rejected(_arguments(**{name: wrong}))
        self.assert_rejected(_arguments(issued_us=None))
        self.assert_rejected(_arguments(expires_us=None))
        for issued, expires, cap in ((100, 100, None), (200, 100, None), (100, 200, 199),
                                     (100, 200, 0), (_MAX, _MAX, None)):
            self.assert_rejected(_arguments(issued_us=issued, expires_us=expires, cap_us=cap))

    def test_owning_snapshot_and_all_public_copies_preserve_original_fingerprint(self):
        supplied = _payload("STOP", peer())
        value = LifecycleDraft(**_arguments("STOP", payload=supplied, cap_us=220))
        fingerprint = value.fingerprint
        supplied["reason"] = "changed caller"
        supplied.clear()
        fields, payload = value.fields, value.payload
        fields["sender_instance"] = request(99)
        fields["payload"]["reason"] = "changed public fields"
        payload["stop_revision"] = "2"
        self.assertEqual(value.payload, _payload("STOP", peer()))
        self.assertEqual(value.fingerprint, fingerprint)
        self.assertEqual(value.fields["sender_instance"], peer().service_instance)
        self.assertIsNot(value.fields, value.fields)
        self.assertIsNot(value.payload, value.payload)
        for name, replacement in (("kind", "HEARTBEAT"), ("request_id", request(9)),
                                  ("issued_us", 0), ("cap_us", None), ("_payload", {})):
            with self.subTest(name=name), self.assertRaises(FrozenInstanceError):
                setattr(value, name, replacement)

    def test_fingerprint_includes_original_context_kind_request_body_times_and_cap(self):
        original = LifecycleDraft(**_arguments("STOP", cap_us=220))
        same = LifecycleDraft(**_arguments("STOP", cap_us=220))
        self.assertIsNot(original, same)
        self.assertNotEqual(original, same)  # 내부 접수 identity와 값 fingerprint는 서로 다르다.
        self.assertEqual(original.fingerprint, same.fingerprint)
        changed_peer = replace(peer(), channel_epoch=request(21))
        changed_body = _payload("STOP", peer())
        changed_body["reason"] = "second intent"
        variants = (_arguments("STOP", current=changed_peer, cap_us=220),
                    _arguments("HEARTBEAT", cap_us=220), _arguments("STOP", request_id=request(22), cap_us=220),
                    _arguments("STOP", issued_us=101, cap_us=220), _arguments("STOP", expires_us=201, cap_us=220),
                    _arguments("STOP", cap_us=221), _arguments("STOP", payload=changed_body, cap_us=220))
        self.assertIs(type(original.fingerprint), tuple)
        for arguments in variants:
            self.assertNotEqual(original.fingerprint, LifecycleDraft(**arguments).fingerprint)
        # cap은 wire11field에 없더라도 held 중복 판정의 원본 일부다.
        changed_cap = LifecycleDraft(**_arguments("STOP", cap_us=221))
        self.assertEqual(original.fields, changed_cap.fields)

    def test_new_peer_cannot_rebase_an_existing_draft_or_its_returned_fields(self):
        old = LifecycleDraft(**_arguments())
        old_fields, fingerprint = old.fields, old.fingerprint
        newer = replace(peer(), channel_epoch=request(55), worker_instance=request(56), release_sha256="b" * 64)
        fresh = LifecycleDraft(**_arguments(current=newer))
        exported = old.fields
        exported["channel_epoch"] = newer.channel_epoch
        exported["payload"]["worker_instance"] = newer.worker_instance
        self.assertEqual(old.fields, old_fields)
        self.assertEqual(old.fingerprint, fingerprint)
        self.assertEqual(old.payload["release_sha256"], "a" * 64)
        self.assertNotEqual(old.fingerprint, fresh.fingerprint)

    def test_flat_guard_rejects_containers_numbers_cycles_and_oversize_before_semantic_helpers(self):
        cycle = {}
        cycle["self"] = cycle
        huge = "x" * 1_000_000
        enormous = 1 << 1_000_000
        invalid = (None, [], (), b"payload", {str(index): None for index in range(6)},
                   {1: None}, {None: "x"}, {True: None},
                   {huge: None}, {"reason": huge}, {"reason": cycle}, {"reason": []},
                   {"reason": b"x"}, {"reason": 1}, {"reason": enormous},
                   {"reason": 0.0}, {"reason": float("nan")}, {"reason": float("inf")})
        with patch.object(tx_outbox, "_context", side_effect=AssertionError("native guard must run first")), \
                patch.object(tx_outbox, "_payload_profile", side_effect=AssertionError("native guard must run first")):
            for index, payload in enumerate(invalid):
                with self.subTest(index=index):
                    self.assert_rejected(_arguments("STOP", payload=payload))

    def test_native_subclasses_and_custom_keys_values_do_not_run_user_hooks(self):
        calls = []

        def trap(name):
            calls.append(name)
            raise AssertionError("unsupported native value executed " + name)

        class DictTrap(dict):
            def __len__(self):
                return trap("dict.__len__")

            def items(self):
                return trap("dict.items")

            def __iter__(self):
                return trap("dict.__iter__")

        class StrTrap(str):
            def __len__(self):
                return trap("str.__len__")

            def __eq__(self, other):
                return trap("str.__eq__")

            def __hash__(self):
                calls.append("str.__hash__")
                return str.__hash__(self)

            def __deepcopy__(self, memo):
                return trap("str.__deepcopy__")

        class ObjectTrap:
            def __hash__(self):
                calls.append("object.__hash__")
                return 47

            def __eq__(self, other):
                return trap("object.__eq__")

            def __str__(self):
                return trap("object.__str__")

            def __deepcopy__(self, memo):
                return trap("object.__deepcopy__")

        class TypeTrap(type):
            def __eq__(self, other):
                return trap("metaclass.__eq__")

            def __hash__(self):
                return trap("metaclass.__hash__")

        class TypedObject(metaclass=TypeTrap):
            pass

        bad_payloads = (DictTrap(reason="x"), {StrTrap("reason"): "x"},
                        {ObjectTrap(): "x"}, {"reason": StrTrap("x")},
                        {"reason": ObjectTrap()}, {"reason": TypedObject()})
        calls.clear()  # fixture의 custom key 삽입은 검사 호출 밖이며 검사 자체의 hook만 센다.
        for index, payload in enumerate(bad_payloads):
            with self.subTest(index=index):
                self.assert_rejected(_arguments("STOP", payload=payload))
        for name, value in (("kind", StrTrap("HELLO")), ("kind", ObjectTrap()),
                            ("receiver_role", StrTrap("S")), ("request_id", StrTrap(request(4))),
                            ("peer", ObjectTrap()), ("issued_us", TypedObject()),
                            ("cap_us", TypedObject())):
            with self.subTest(name=name):
                arguments = _arguments()
                arguments[name] = value
                self.assert_rejected(arguments)
        self.assertEqual(calls, [])

    def test_oversize_top_level_strings_are_rejected_before_uuid_or_profile_calls(self):
        huge = "x" * 1_000_000
        with patch.object(tx_outbox, "_uuid", side_effect=AssertionError("size guard must run first")), \
                patch.object(tx_outbox, "_context", side_effect=AssertionError("size guard must run first")):
            for name in ("kind", "receiver_role", "request_id"):
                self.assert_rejected(_arguments(**{name: huge}))

    def test_every_kind_requires_exact_payload_keys_not_opaque_padding(self):
        for kind in _KINDS:
            baseline = _payload(kind, peer())
            for key in baseline:
                incomplete = baseline.copy()
                del incomplete[key]
                with self.subTest(kind=kind, missing=key):
                    self.assert_rejected(_arguments(kind, payload=incomplete))
            unknown = baseline.copy()
            unknown["padding"] = "x"
            self.assert_rejected(_arguments(kind, payload=unknown))

    def test_hello_pinned_role_worker_release_and_zero_capability_are_not_repaired(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            baseline = _payload("HELLO", current)
            for name, wrong in (("role", "S"), ("role", role.lower()), ("role", True),
                                ("worker_instance", request(99)), ("worker_instance", None),
                                ("release_sha256", "b" * 64), ("release_sha256", "A" * 64),
                                ("release_sha256", "a" * 63), ("capability_mask", "1"),
                                ("capability_mask", "00"), ("capability_mask", 0)):
                payload = baseline.copy()
                payload[name] = wrong
                with self.subTest(role=role, name=name):
                    self.assert_rejected(_arguments("HELLO", current, payload=payload))

    def test_heartbeat_role_states_are_statements_without_hello_or_actual_progress_authority(self):
        for role, states in _STATES.items():
            current = peer(role)
            for state in states:
                payload = dict(worker_state=state, last_progress_sequence=None, last_progress_j_us=None)
                value = LifecycleDraft(**_arguments("HEARTBEAT", current, payload=payload))
                self.assertEqual(value.payload, payload)
        for role, state in (("A", "RUNNING"), ("M", "CLOSED"), ("D", "WAIT_STORAGE")):
            payload = dict(worker_state=state, last_progress_sequence=None, last_progress_j_us=None)
            self.assert_rejected(_arguments("HEARTBEAT", peer(role), payload=payload))
        for state in ("UNKNOWN", "starting", None, True, 1):
            payload = dict(worker_state=state, last_progress_sequence=None, last_progress_j_us=None)
            self.assert_rejected(_arguments("HEARTBEAT", payload=payload))

    def test_heartbeat_completed_tuple_respects_original_issue_not_later_encode_time(self):
        for sequence, completed, issued, expires in ((None, None, 100, 200), ("0", "0", 0, 1),
                                                     ("9", "100", 100, 200), (str(_MAX), str(_MAX - 1), _MAX - 1, _MAX)):
            payload = dict(worker_state="STARTING", last_progress_sequence=sequence, last_progress_j_us=completed)
            value = LifecycleDraft(**_arguments("HEARTBEAT", payload=payload, issued_us=issued, expires_us=expires))
            self.assertEqual(value.payload, payload)
        for sequence, completed in ((None, "0"), ("0", None), ("1", "101")):
            payload = dict(worker_state="STARTING", last_progress_sequence=sequence, last_progress_j_us=completed)
            self.assert_rejected(_arguments("HEARTBEAT", payload=payload))
        for name in ("last_progress_sequence", "last_progress_j_us"):
            for wrong in ("", "01", "-1", "+1", " 1", "1.0", str(_MAX + 1), "١", 1, True, 1.0):
                payload = dict(worker_state="STARTING", last_progress_sequence="1", last_progress_j_us="100")
                payload[name] = wrong
                self.assert_rejected(_arguments("HEARTBEAT", payload=payload))

    def test_stop_and_stopped_require_positive_canonical_revision_without_wire_sequence(self):
        for kind in ("STOP", "STOPPED"):
            for revision in ("1", str(_MAX)):
                payload = _payload(kind, peer())
                payload["stop_revision"] = revision
                value = LifecycleDraft(**_arguments(kind, payload=payload))
                self.assertEqual(value.payload["stop_revision"], revision)
                self.assertNotIn("message_sequence", value.fields)
            for wrong in ("0", "", "01", "-1", "1.0", str(_MAX + 1), 1, True, None):
                payload = _payload(kind, peer())
                payload["stop_revision"] = wrong
                self.assert_rejected(_arguments(kind, payload=payload))

    def test_stop_drain_endpoints_and_printable_reason_are_preserved_without_encoder(self):
        for drain in ("100", "200"):
            for reason in ("x", " " * 32, '"' * 32, "\\" * 32, "~"):
                payload = dict(stop_revision="1", reason=reason, drain_deadline_j_us=drain)
                value = LifecycleDraft(**_arguments("STOP", payload=payload))
                self.assertEqual(value.payload, payload)
        for drain in ("99", "201", "01", "-1", str(_MAX + 1), 100, True, None):
            payload = _payload("STOP", peer())
            payload["drain_deadline_j_us"] = drain
            self.assert_rejected(_arguments("STOP", payload=payload))
        for reason in ("", "x" * 33, "\n", "\x00", "\x7f", "중단", "\ud800", True, None):
            payload = _payload("STOP", peer())
            payload["reason"] = reason
            self.assert_rejected(_arguments("STOP", payload=payload))

    def test_stopped_success_and_failed_partial_reports_are_distinct_not_exit_facts(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            for released in (False, True):
                for closed in ((False, True) if role == "A" else (None,)):
                    payload = dict(stop_revision="1", leases_released=released, device_closed=closed,
                                   checkpoint_job=None, status="FAILED")
                    value = LifecycleDraft(**_arguments("STOPPED", current, payload=payload))
                    self.assertEqual(value.payload, payload)
                    payload["status"] = "STOPPED"
                    if released and (role != "A" or closed):
                        self.assertEqual(LifecycleDraft(**_arguments("STOPPED", current, payload=payload)).payload, payload)
                    else:
                        self.assert_rejected(_arguments("STOPPED", current, payload=payload))

    def test_stopped_role_specific_bool_null_and_checkpoint_deferred_contract(self):
        for role in ("A", "M", "D"):
            current = peer(role)
            wrongs = [("leases_released", value) for value in (0, 1, "true", None)]
            wrongs += [("device_closed", value) for value in ((None, 0, 1, "true") if role == "A" else (True, False, "null"))]
            wrongs += [("checkpoint_job", value) for value in (request(8), "", False, {})]
            wrongs += [("status", value) for value in ("CLOSED", "FAILED ", "failed", True, None)]
            for name, wrong in wrongs:
                payload = _payload("STOPPED", current)
                payload["status"] = "FAILED"
                payload[name] = wrong
                with self.subTest(role=role, name=name):
                    self.assert_rejected(_arguments("STOPPED", current, payload=payload))


if __name__ == "__main__":
    unittest.main()
