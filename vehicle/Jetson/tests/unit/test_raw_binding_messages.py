"""합성 RAW BIND/ACK의 형식·원 요청 결합. 실제 전송/등록 변경/준비 권한은 시험하지 않는다."""

from dataclasses import replace
import hashlib
import json
import unittest

from raw_lease_fixtures import identifier
from raw_binding_fixtures import (
    ack_fields, ack_packet, bind_fields, bind_message, bind_packet, context,
)
from xenova_vehicle.runtime.local_envelope import decode_structure
from xenova_vehicle.runtime.raw_binding import RawBinding, RawBindingProfile
from xenova_vehicle.runtime.raw_binding_messages import (
    RawBindAck, RawBindContext, RawBindMessage, RawBindMessageError,
    parse_raw_bind, parse_raw_bind_ack,
)


_MAX = 2**64 - 1
_POSITIVE_STATES = {
    "A": "STARTING ASSET_CHECK DEVICE_BOOT PIPELINE_START WARMUP STREAMING DEGRADED".split(),
    "M": "STARTING WAIT_INPUT WAIT_CLOCK WAIT_ODOM WAIT_STORAGE WARMUP RUNNING DEGRADED RECOVERING".split(),
}
_ALL_STATES = {
    "A": _POSITIVE_STATES["A"] + "DRAINING CLOSING CLOSED FAILED FAILED_CONFIG FAILED_NEEDS_SERVICE".split(),
    "M": "STARTING WAIT_INPUT WAIT_CLOCK WAIT_ODOM WAIT_STORAGE WARMUP RUNNING DEGRADED QUIESCING RECOVERING FAILED STOPPED".split(),
}
_REASONS = ("UNSUPPORTED_PROFILE", "ASSET_UNAVAILABLE", "LOCAL_SETUP_FAILED")
_PROFILE_FIELDS = ("device_id", "clock_epoch", "model_id", "parser_id", "graph_profile_sha256",
                   "format_table_sha256", "calibration_bundle", "nn_enabled", "jpeg_enabled")


def _packet(fields):
    """b1 구조만 통과시킨다. typed encoder/ACK 수락 함수를 oracle로 쓰지 않는다."""
    return decode_structure(json.dumps(fields, sort_keys=True, ensure_ascii=True,
                                       separators=(",", ":"), allow_nan=False).encode("ascii"))


def _profile_changed(profile, **changes):
    values = {name: getattr(profile, name) for name in _PROFILE_FIELDS}
    values.update(changes)
    return RawBindingProfile(**values)


class RawBindingMessageTests(unittest.TestCase):
    def assert_bind_bad(self, fields, ctx, *, cap_us=None):
        packet = _packet(fields)
        for entry in (parse_raw_bind, RawBindMessage):
            with self.subTest(entry=entry.__name__), self.assertRaises(RawBindMessageError):
                entry(packet, ctx, cap_us=cap_us)

    def assert_ack_bad(self, fields, original):
        packet = _packet(fields)
        for entry in (parse_raw_bind_ack, RawBindAck):
            with self.subTest(entry=entry.__name__), self.assertRaises(RawBindMessageError):
                entry(packet, original)

    def test_bind_and_ack_for_both_roles_and_nn_jpeg_profiles_are_statements_only(self):
        for role in ("A", "M"):
            for nn_enabled, jpeg_enabled in ((False, False), (False, True), (True, False), (True, True)):
                ctx = context(role=role, nn_enabled=nn_enabled, jpeg_enabled=jpeg_enabled)
                packet = bind_packet(ctx)
                for entry in (parse_raw_bind, RawBindMessage):
                    with self.subTest(role=role, nn=nn_enabled, jpeg=jpeg_enabled, entry=entry.__name__):
                        original = entry(packet, ctx)
                        self.assertIs(original.context, ctx)
                        self.assertIs(original.packet, packet)
                        self.assertEqual(original.raw, packet.raw_bytes)
                        self.assertEqual(original.fields, packet.fields)
                        self.assertEqual(set(original.payload), {"binding_revision", "binding_sha256", "binding"})
                        self.assertEqual(original.binding.sha256, original.payload["binding_sha256"])
                        self.assertEqual(original.binding_revision, 1)
                        response = parse_raw_bind_ack(ack_packet(original), original)
                        self.assertIs(response.original_bind, original)
                        self.assertIs(response.context, ctx)
                        self.assertEqual(response.binding.sha256, original.binding.sha256)
                        self.assertTrue(response.accepted)
                        self.assertIsNone(response.reason)
                        self.assertEqual(response.request_id, original.request_id)
                        self.assertFalse(any(hasattr(response, name) for name in (
                            "nn_ready", "metric_ready", "capture_ready", "authorized", "registered", "highwater")))

    def test_bind_uses_s_sender_target_channel_and_registered_acquisition_not_mapper_worker(self):
        for role in ("A", "M"):
            ctx = context(role=role)
            original = bind_message(ctx)
            fields = original.fields
            self.assertEqual(fields["sender_instance"], ctx.peer.service_instance)
            self.assertEqual(fields["channel_epoch"], ctx.peer.channel_epoch)
            self.assertEqual(original.binding.fields["acquisition_worker"], ctx.acquisition.worker_instance)
            if role == "M":
                self.assertNotEqual(ctx.peer.worker_instance, ctx.acquisition.worker_instance)
                self.assertNotEqual(ctx.peer.channel_epoch, ctx.acquisition.channel_epoch)
                changed = original.binding.fields
                changed["acquisition_worker"] = ctx.peer.worker_instance
                self.assert_bind_bad(bind_fields(ctx, binding=RawBinding(changed)), ctx)
            for name, wrong in (("sender_instance", ctx.peer.worker_instance),
                                ("service_instance", identifier(990)), ("channel_epoch", identifier(991))):
                changed = original.fields
                changed[name] = wrong
                self.assert_bind_bad(changed, ctx)
            self.assert_bind_bad(bind_fields(ctx, revision=1), context(role=role, nn_enabled=False))

    def test_context_requires_a_target_match_and_m_target_separation_in_each_endpoint_component(self):
        for role in ("A", "M"):
            ctx = context(role=role)
            for name in ("channel_epoch", "worker_instance"):
                if role == "A":
                    endpoint = replace(ctx.acquisition, **{name: identifier(991)})
                else:
                    endpoint = replace(ctx.acquisition, **{name: getattr(ctx.peer, name)})
                with self.subTest(role=role, name=name), self.assertRaises(RawBindMessageError):
                    RawBindContext(ctx.peer, endpoint, ctx.profile)
        ctx = context()
        with self.assertRaises(RawBindMessageError):
            RawBindContext(replace(ctx.peer, role="D"), ctx.acquisition, ctx.profile)

    def test_independent_profile_refs_cannot_be_replaced_by_self_consistent_body_hash(self):
        ctx = context()
        original = bind_message(ctx)
        changes = {"device_id": ctx.profile.device_id.swapcase(), "clock_epoch": identifier(990),
                   "model_id": "e" * 64, "parser_id": "f" * 64,
                   "graph_profile_sha256": "1" * 64, "format_table_sha256": "2" * 64,
                   "calibration_bundle": "3" * 64}
        for name, changed in changes.items():
            body = original.binding.fields
            body[name] = changed
            value = RawBinding(body)
            candidate = bind_fields(ctx, binding=value)
            self.assertEqual(candidate["payload"]["binding_sha256"], value.sha256)
            with self.subTest(name=name):
                self.assert_bind_bad(candidate, ctx)
            approved = RawBindContext(ctx.peer, ctx.acquisition, _profile_changed(ctx.profile, **{name: changed}))
            self.assertEqual(parse_raw_bind(_packet(candidate), approved).binding.sha256, value.sha256)

    def test_m_only_context_change_preserves_raw_binding_hash_without_mapper_field(self):
        ctx = context(role="M")
        previous = bind_message(ctx)
        changed_peer = replace(ctx.peer, channel_epoch=identifier(990), worker_instance=identifier(991))
        newer = RawBindContext(changed_peer, ctx.acquisition, ctx.profile)
        changed_fields = previous.fields
        changed_fields["channel_epoch"] = newer.peer.channel_epoch
        current = parse_raw_bind(_packet(changed_fields), newer)
        self.assertEqual(current.binding.sha256, previous.binding.sha256)
        self.assertEqual(current.binding.fields, previous.binding.fields)
        self.assertNotIn("mapper_worker", current.binding.fields)
        self.assertNotIn("version", current.binding.fields)
        self.assert_bind_bad(changed_fields, ctx)

    def test_raw_domain_positive_matching_revision_and_exact_three_field_payload(self):
        ctx = context()
        for revision in (1, _MAX):
            original = bind_message(ctx, revision=revision)
            self.assertEqual(original.binding_revision, revision)
            self.assertEqual(original.fields["binding_revision"], str(revision))
        for domain in ("LIFECYCLE", "PUBLICATION", "STORAGE"):
            bad = bind_fields(ctx)
            bad["binding_domain"] = domain
            self.assert_bind_bad(bad, ctx)
        bad = bind_fields(ctx)
        bad["binding_revision"] = "0"
        bad["payload"]["binding_revision"] = "0"
        self.assert_bind_bad(bad, ctx)
        for wrong in ("0", "2", "01", str(_MAX + 1), 1, True, None):
            bad = bind_fields(ctx)
            bad["payload"]["binding_revision"] = wrong
            self.assert_bind_bad(bad, ctx)
        baseline = bind_fields(ctx)
        for name in tuple(baseline["payload"]):
            bad = deepcopy_fields(baseline)
            del bad["payload"][name]
            self.assert_bind_bad(bad, ctx)
        bad = bind_fields(ctx)
        bad["payload"]["unknown"] = None
        self.assert_bind_bad(bad, ctx)
        for wrong in ("f" * 64, "A" * 64, "a" * 63, None, True):
            bad = bind_fields(ctx)
            bad["payload"]["binding_sha256"] = wrong
            self.assert_bind_bad(bad, ctx)

    def test_original_bind_half_open_window_five_second_limit_and_external_cap(self):
        ctx = context()
        for issued, expires in ((0, 1), (100, 5000100), (_MAX - 5000000, _MAX)):
            packet = bind_packet(ctx, issued=issued, expires=expires)
            for entry in (parse_raw_bind, RawBindMessage):
                original = entry(packet, ctx, cap_us=expires)
                self.assertEqual((original.issued_us, original.expires_us, original.cap_us), (issued, expires, expires))
        for issued, expires in ((100, 100), (200, 100), (100, 5000101)):
            self.assert_bind_bad(bind_fields(ctx, issued=issued, expires=expires), ctx)
        self.assert_bind_bad(bind_fields(ctx), ctx, cap_us=5000099)
        for bad in (True, False, -1, _MAX + 1, 5000100.0, "5000100", object()):
            packet = bind_packet(ctx)
            for entry in (parse_raw_bind, RawBindMessage):
                with self.assertRaises(RawBindMessageError):
                    entry(packet, ctx, cap_us=bad)

    def test_ack_lower_sequence_is_valid_because_sender_changes_and_no_hwm_is_claimed(self):
        original = bind_message(sequence=100)
        for sequence in (1, 100, _MAX):
            packet = ack_packet(original, sequence=sequence)
            for entry in (parse_raw_bind_ack, RawBindAck):
                response = entry(packet, original)
                self.assertEqual(response.fields["message_sequence"], str(sequence))
                self.assertEqual(response.fields["sender_instance"], original.context.peer.worker_instance)
                self.assertEqual(response.fields["channel_epoch"], original.fields["channel_epoch"])
                self.assertNotEqual(response.fields["sender_instance"], original.fields["sender_instance"])

    def test_ack_is_bound_to_original_request_peer_revision_hash_and_raw_domain(self):
        for role in ("A", "M"):
            original = bind_message(context(role=role))
            edits = (("request_id", identifier(991)), ("sender_instance", original.context.peer.service_instance),
                     ("service_instance", identifier(992)), ("channel_epoch", identifier(993)),
                     ("binding_domain", "LIFECYCLE"), ("binding_revision", "2"))
            for name, wrong in edits:
                bad = ack_fields(original)
                bad[name] = wrong
                self.assert_ack_bad(bad, original)
            for name, wrong in (("binding_revision", "2"), ("binding_revision", "0"),
                                ("binding_sha256", "f" * 64), ("binding_sha256", "A" * 64)):
                bad = ack_fields(original)
                bad["payload"][name] = wrong
                self.assert_ack_bad(bad, original)
            for name in tuple(ack_fields(original)["payload"]):
                bad = ack_fields(original)
                del bad["payload"][name]
                self.assert_ack_bad(bad, original)
            bad = ack_fields(original)
            bad["payload"]["binding"] = original.binding.fields
            self.assert_ack_bad(bad, original)

    def test_ack_window_is_inside_original_bind_without_new_five_second_budget(self):
        original = bind_message(issued=100, expires=400)
        for issued, expires in ((100, 101), (200, 400), (399, 400)):
            packet = ack_packet(original, issued=issued, expires=expires)
            result = parse_raw_bind_ack(packet, original)
            self.assertEqual((result.issued_us, result.expires_us), (issued, expires))
        for issued, expires in ((99, 200), (200, 200), (300, 200), (400, 401), (200, 401)):
            self.assert_ack_bad(ack_fields(original, issued=issued, expires=expires), original)
        near_max = bind_message(issued=_MAX - 10, expires=_MAX)
        result = RawBindAck(ack_packet(near_max, issued=_MAX - 1, expires=_MAX), near_max)
        self.assertEqual(result.expires_us, _MAX)

    def test_positive_ack_states_are_role_specific_without_ready_or_state_transition(self):
        for role, states in _POSITIVE_STATES.items():
            original = bind_message(context(role=role))
            for state in states:
                result = parse_raw_bind_ack(ack_packet(original, state=state), original)
                self.assertTrue(result.accepted)
                self.assertEqual(result.state, state)
                self.assertIsNone(result.reason)
                self.assertIs(result.original_bind, original)
            for state in set(_ALL_STATES[role]) - set(states):
                self.assert_ack_bad(ack_fields(original, state=state), original)
        for role, wrong in (("A", "WAIT_CLOCK"), ("M", "STREAMING")):
            original = bind_message(context(role=role))
            self.assert_ack_bad(ack_fields(original, state=wrong), original)

    def test_negative_ack_all_role_diagnostic_states_and_exact_refusal_reasons(self):
        for role, states in _ALL_STATES.items():
            original = bind_message(context(role=role))
            before = (original.raw, original.binding.canonical_bytes, original.context)
            for state in states:
                for reason in _REASONS:
                    result = RawBindAck(ack_packet(original, accepted=False, state=state, reason=reason), original)
                    self.assertIs(result.accepted, False)
                    self.assertEqual((result.state, result.reason), (state, reason))
                    self.assertEqual((original.raw, original.binding.canonical_bytes, original.context), before)
        original = bind_message()
        for accepted, wrong in ((True, "UNSUPPORTED_PROFILE"), (True, ""), (False, None),
                                (False, ""), (False, "unsupported_profile"), (False, "TIMEOUT"),
                                (False, "UNSUPPORTED_PROFILE "), (False, "x" * 128),
                                (False, True), (False, 1)):
            bad = ack_fields(original, accepted=accepted)
            bad["payload"]["reason"] = wrong
            self.assert_ack_bad(bad, original)

    def test_ack_accepted_exact_bool_and_state_type_unknown_are_not_coerced(self):
        original = bind_message()
        for wrong in (0, 1, "true", "false", None, [], {}):
            bad = ack_fields(original)
            bad["payload"]["accepted"] = wrong
            self.assert_ack_bad(bad, original)
        for wrong in (None, True, 1, "", "BOUND", "starting", "UNKNOWN", "x" * 128):
            bad = ack_fields(original)
            bad["payload"]["state"] = wrong
            self.assert_ack_bad(bad, original)

    def test_wire_ascii_escape_key_order_whitespace_do_not_change_canonical_body_hash(self):
        ctx = context()
        fields = bind_fields(ctx)
        canonical = RawBinding(fields["payload"]["binding"])
        wire = json.dumps(fields, sort_keys=False, ensure_ascii=True, indent=2).encode("ascii")
        wire = wire.replace(b'"pipeline_instance"', b'"pipeline_\\u0069nstance"', 1)
        packet = decode_structure(wire)
        result = parse_raw_bind(packet, ctx)
        self.assertEqual(result.raw, wire)
        self.assertEqual(result.binding.canonical_bytes, canonical.canonical_bytes)
        self.assertEqual(result.binding.sha256, hashlib.sha256(canonical.canonical_bytes).hexdigest())
        self.assertNotEqual(result.binding.sha256, hashlib.sha256(wire).hexdigest())
        response_fields = ack_fields(result)
        response_wire = json.dumps(response_fields, indent=2).encode("ascii")
        response = RawBindAck(decode_structure(response_wire), result)
        self.assertEqual(response.raw, response_wire)
        self.assertEqual(response.binding.sha256, canonical.sha256)

    def test_full_packet_4096_positive_and_4097_structure_rejection_use_actual_b1_cap(self):
        ctx = context()
        compact = json.dumps(bind_fields(ctx), sort_keys=True, separators=(",", ":")).encode("ascii")
        self.assertLess(len(compact), 4096)
        wire = b"{" + b" " * (4096 - len(compact)) + compact[1:]
        self.assertEqual(len(wire), 4096)
        result = RawBindMessage(decode_structure(wire), ctx)
        self.assertEqual(result.raw, wire)
        self.assertEqual(result.binding.sha256, bind_message(ctx).binding.sha256)
        with self.assertRaises(ValueError):
            decode_structure(wire[:1] + b" " + wire[1:])

    def test_public_payload_fields_binding_copies_cannot_modify_original_bind_or_ack(self):
        ctx = context()
        original = bind_message(ctx)
        response = RawBindAck(ack_packet(original), original)
        before = (original.raw, original.binding.canonical_bytes, original.binding.sha256, response.raw)
        first = original.fields
        first["payload"]["binding"]["stream_instances"]["rgb_video"] = identifier(991)
        first["payload"]["binding_sha256"] = "f" * 64
        original.payload["binding"]["device_id"] = "changed"
        original.binding.fields["stream_instances"].clear()
        response.fields["payload"]["accepted"] = False
        response.payload["reason"] = "LOCAL_SETUP_FAILED"
        self.assertEqual((original.raw, original.binding.canonical_bytes, original.binding.sha256, response.raw), before)
        self.assertTrue(response.accepted)
        self.assertIsNone(response.reason)
        self.assertIsNot(original.fields, original.fields)
        self.assertIsNot(original.payload, original.payload)
        self.assertIsNot(response.payload, response.payload)
        for value, name in ((original, "cap_us"), (original, "packet"), (response, "original_bind"),
                            (ctx, "peer"), (ctx.profile, "device_id")):
            with self.assertRaises((AttributeError, TypeError)):
                setattr(value, name, None)

    def test_direct_constructors_revalidate_and_do_not_accept_unchecked_or_wrong_packet_inputs(self):
        ctx = context()
        original = bind_message(ctx)
        response = ack_packet(original)
        for entry, args in ((RawBindMessage, (original.packet, ctx)), (RawBindAck, (response, original))):
            for name in ("_fields", "_validated", "validated", "_binding", "binding"):
                with self.assertRaises(TypeError):
                    entry(*args, **{name: True})
        for bad in (None, original.raw, original.fields, object()):
            for entry in (RawBindMessage, parse_raw_bind):
                with self.assertRaises(RawBindMessageError):
                    entry(bad, ctx)
            for entry in (RawBindAck, parse_raw_bind_ack):
                with self.assertRaises(RawBindMessageError):
                    entry(bad, original)
        for bad in (None, ctx, original.fields, original.packet):
            for entry in (RawBindAck, parse_raw_bind_ack):
                with self.assertRaises(RawBindMessageError):
                    entry(response, bad)
        class PacketSubclass(type(original.packet)):
            pass

        class BindSubclass(RawBindMessage):
            pass

        foreign_packet = PacketSubclass(original.raw)
        for entry in (RawBindMessage, parse_raw_bind):
            with self.assertRaises(RawBindMessageError):
                entry(foreign_packet, ctx)
        inherited = BindSubclass(original.packet, ctx)
        for entry in (RawBindAck, parse_raw_bind_ack):
            with self.assertRaises(RawBindMessageError):
                entry(response, inherited)
        wrong_kind = original.fields
        wrong_kind["kind"] = "BIND_ACK"
        self.assert_bind_bad(wrong_kind, ctx)
        wrong_kind = ack_fields(original)
        wrong_kind["kind"] = "BIND"
        self.assert_ack_bad(wrong_kind, original)

    def test_native_context_requires_exact_records_without_custom_type_hooks(self):
        ctx = context()
        calls = []

        class TypeTrap(type):
            def __eq__(self, other):
                calls.append("type.eq")
                raise AssertionError("native context metaclass equality must not run")

            def __hash__(self):
                calls.append("type.hash")
                raise AssertionError("native context metaclass hash must not run")

        class ObjectTrap(metaclass=TypeTrap):
            def __str__(self):
                calls.append("object.str")
                raise AssertionError("native context string conversion must not run")

        class EndpointSubclass(type(ctx.acquisition)):
            pass

        class PeerSubclass(type(ctx.peer)):
            pass

        inherited = EndpointSubclass(ctx.acquisition.channel_epoch, ctx.acquisition.worker_instance)
        inherited_peer = PeerSubclass(ctx.peer.service_instance, ctx.peer.channel_epoch,
                                     ctx.peer.worker_instance, ctx.peer.role, ctx.peer.release_sha256)
        for args in ((ObjectTrap(), ctx.acquisition, ctx.profile),
                     (ctx.peer, ObjectTrap(), ctx.profile), (ctx.peer, ctx.acquisition, ObjectTrap()),
                     (ctx.peer, inherited, ctx.profile), (inherited_peer, ctx.acquisition, ctx.profile)):
            with self.assertRaises(RawBindMessageError):
                RawBindContext(*args)
        packet = bind_packet(ctx)
        for bad in (ObjectTrap(), None, {}):
            for entry in (RawBindMessage, parse_raw_bind):
                with self.assertRaises(RawBindMessageError):
                    entry(packet, bad)
        self.assertEqual(calls, [])


def deepcopy_fields(value):
    """이 fixture helper에는 검증된 JSON literal만 넘긴다. native hook 시험에는 쓰지 않는다."""
    return json.loads(json.dumps(value))


if __name__ == "__main__":
    unittest.main()
