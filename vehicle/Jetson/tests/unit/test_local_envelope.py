"""outer 구조·opaque JSON·유한 encode work 검사. peer/payload/admission 시험이 아니다."""

from dataclasses import FrozenInstanceError
import json
import math
import unittest
from unittest.mock import patch

from envelope_fixtures import fields, raw
from xenova_vehicle.runtime.local_envelope import (
    EnvelopeStructureError, StructuralPacket, decode_structure, encode_structure,
)


_KINDS = ("HELLO", "BIND", "BIND_ACK", "PROGRESS", "HEARTBEAT", "SLOT", "DB_JOB", "FAULT", "STOP", "STOPPED")
_DOMAINS = ("RAW", "PUBLICATION", "STORAGE", "LIFECYCLE")
_U64_MAX = "18446744073709551615"


def literal(value):
    """음성 JSON도 만들 수 있는 독립 fixture encoder. 제품 encoder를 호출하지 않는다."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                      allow_nan=True).encode("ascii")


def nested(containers):
    value = "scalar"
    for index in range(containers):
        value = {"key": value} if index % 2 else [value]
    return value


class LocalEnvelopeStructureTests(unittest.TestCase):
    def assert_fields_invalid(self, value):
        with self.assertRaises(EnvelopeStructureError):
            encode_structure(value)
        with self.assertRaises(EnvelopeStructureError):
            decode_structure(literal(value))

    def test_all40_kind_domain_combinations_are_structural_opaque_only(self):
        for kind in _KINDS:
            for domain in _DOMAINS:
                with self.subTest(kind=kind, domain=domain):
                    value = fields(kind, payload={"unregistered_op": "NOT_A_TYPED_CONTRACT", "role": "unknown"}, domain=domain)
                    packet = encode_structure(value)
                    self.assertIsInstance(packet, StructuralPacket)
                    self.assertEqual(packet.raw_bytes, raw(kind, payload=value["payload"], domain=domain))
                    self.assertEqual(decode_structure(packet.raw_bytes).fields, value)
                    self.assertEqual((packet.kind, packet.binding_domain), (kind, domain))
                    self.assertFalse(hasattr(packet, "authorized") or hasattr(packet, "ready"))

    def test_u64_boundaries_and_nil_uuid_are_structural_not_admission(self):
        for sequence in ("1", _U64_MAX):
            value = fields()
            value.update(message_sequence=sequence, binding_revision=_U64_MAX,
                         issued_j_us=_U64_MAX, expires_j_us="0")
            for name in ("service_instance", "channel_epoch", "sender_instance", "request_id"):
                value[name] = "00000000-0000-0000-0000-000000000000"
            self.assertEqual(decode_structure(literal(value)).fields, value)
            self.assertEqual(encode_structure(value).fields, value)
        self.assertEqual(encode_structure(fields()).fields["binding_revision"], "0")

    def test_finite_unicode_control_and_opaque_payload_types_are_preserved(self):
        payload = {"한글": "🌍", "control": "\x00\b\f\n\r\t\\\"", "bool": True,
                   "null": None, "int": -17, "float": 1.25, "signed_zero": -0.0,
                   "array": [False, "text", {"nested": 0}], "é": 1, "e\u0301": 2}
        packet = encode_structure(fields(payload=payload))
        self.assertIn("한글".encode("utf-8"), packet.raw_bytes)
        self.assertIn("🌍".encode("utf-8"), packet.raw_bytes)
        self.assertEqual(packet.raw_bytes, raw(payload=payload))
        self.assertEqual(decode_structure(packet.raw_bytes).payload, payload)
        self.assertEqual(math.copysign(1, packet.payload["signed_zero"]), -1)

    def test_decode_preserves_noncanonical_original_bytes_without_reencoding(self):
        original = raw(payload={"x": 1}).replace(b'"version":1', b'"vers\\u0069on" : 1')
        packet = decode_structure(original)
        self.assertEqual(packet.raw_bytes, original)
        canonical = encode_structure(packet.fields)
        self.assertEqual(canonical.fields, packet.fields)
        self.assertNotEqual(canonical.raw_bytes, original)

    def test_decoder_4096_raw_finite_exponent_may_expand_beyond_encoder_cap(self):
        original = raw(payload={"v": 0.0, "pad": ""}).replace(b'"v":0.0', b'"v":1e15')
        original = original.replace(b'"pad":""', b'"pad":"' + b'x' * (4096 - len(original)) + b'"')
        self.assertEqual(len(original), 4096)
        expected = json.loads(original)
        self.assertTrue(math.isfinite(expected["payload"]["v"]))
        self.assertEqual(len(literal(expected)), 4110)
        packet = decode_structure(original)
        self.assertEqual((packet.raw_bytes, packet.fields), (original, expected))
        with self.assertRaises(EnvelopeStructureError):
            encode_structure(packet.fields)

    def test_decoder_compact800_floats_is_not_limited_by_reencoded_length(self):
        original = raw(payload={"a": []}).replace(b'"a":[]', b'"a":[' + b','.join([b'1e9'] * 800) + b']')
        self.assertLess(len(original), 4096)
        expected = json.loads(original)
        self.assertGreater(len(literal(expected)), 4096)
        packet = StructuralPacket(original)
        self.assertEqual(packet.raw_bytes, original)
        self.assertEqual(packet.payload["a"], [1_000_000_000.0] * 800)
        with self.assertRaises(EnvelopeStructureError):
            encode_structure(packet.fields)

    def test_root1_payload2_depth6_accepts_scalars_but_depth7_rejects(self):
        value = fields(payload={"value": nested(4)})
        packet = encode_structure(value)
        self.assertEqual(decode_structure(packet.raw_bytes).fields, value)
        self.assert_fields_invalid(fields(payload={"value": nested(5)}))
        self.assertEqual(encode_structure(fields(payload={"empty": [[[{}]]]})).payload, {"empty": [[[{}]]]})

    def test_exact4096_utf8_bytes_accept_and4097_reject_before_json_parse(self):
        prefix = raw(payload={"pad": "", "unicode": "🌍"})
        padding = "x" * (4096 - len(prefix))
        original = raw(payload={"pad": padding, "unicode": "🌍"})
        self.assertEqual(len(original), 4096)
        self.assertEqual(decode_structure(original).raw_bytes, original)
        self.assertEqual(encode_structure(fields(payload={"pad": padding, "unicode": "🌍"})).raw_bytes, original)
        with patch("xenova_vehicle.runtime.local_envelope.json.loads", side_effect=AssertionError("parser reached")) as parser:
            with self.assertRaises(EnvelopeStructureError):
                decode_structure(original + b"x")
            parser.assert_not_called()

    def test_missing_or_unknown_each_outer_key_is_rejected(self):
        for key in fields():
            value = fields()
            value.pop(key)
            with self.subTest(missing=key):
                self.assert_fields_invalid(value)
        for key in ("unknown", "role", "live_authority", "kind "):
            with self.subTest(extra=key):
                self.assert_fields_invalid({**fields(), key: False})

    def test_version_kind_domain_and_payload_outer_types_are_exact(self):
        for value in (True, False, 0, 2, -1, 1.0, "1", None):
            bad = fields()
            bad["version"] = value
            with self.subTest(version=value):
                self.assert_fields_invalid(bad)
        for name, values in (("kind", ("hello", "HELLO ", "UNKNOWN", 1, True, None)),
                             ("binding_domain", ("raw", "RAW ", "UNKNOWN", 1, False, None)),
                             ("payload", (None, [], "{}", 0, False))):
            for value in values:
                bad = fields()
                bad[name] = value
                with self.subTest(name=name, value=value):
                    self.assert_fields_invalid(bad)

    def test_all_u64_fields_reject_noncanonical_numeric_forms_and_wrong_types(self):
        invalid = ("", "00", "01", "-0", "-1", "+1", " 1", "1 ", "1.0", "1e1",
                   "18446744073709551616", "١", "１", 0, 1, True, 1.0, None)
        for name in ("message_sequence", "binding_revision", "issued_j_us", "expires_j_us"):
            for value in invalid:
                bad = fields()
                bad[name] = value
                with self.subTest(name=name, value=value):
                    self.assert_fields_invalid(bad)
        value = fields()
        value["message_sequence"] = "0"
        self.assert_fields_invalid(value)

    def test_all_uuid_fields_reject_noncanonical_or_nonstring_values(self):
        invalid = ("AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", "00000000000040008000000000000001",
                   "{00000000-0000-4000-8000-000000000001}", "g0000000-0000-4000-8000-000000000001",
                   "00000000-0000-4000-8000-000000000001 ", None, 1, True)
        for name in ("service_instance", "channel_epoch", "sender_instance", "request_id"):
            for value in invalid:
                bad = fields()
                bad[name] = value
                with self.subTest(name=name, value=value):
                    self.assert_fields_invalid(bad)

    def test_duplicate_decoded_keys_are_rejected_at_root_and_nested_array_objects(self):
        original = raw(payload={"box": [{"x": 0}]})
        cases = (b'{"kind":"STOP",' + original[1:],
                 b'{"k\\u0069nd":"STOP",' + original[1:],
                 original.replace(b'"x":0', b'"x":0,"\\u0078":1'),
                 original.replace(b'"x":0', '"é":0,"\\u00e9":1'.encode("utf-8")))
        for value in cases:
            with self.subTest(value=value), self.assertRaises(EnvelopeStructureError):
                decode_structure(value)
        for depth in range(2, 7):
            payload = b'{"x":0,"\\u0078":1}'
            for _ in range(depth - 2):
                payload = b'{"box":' + payload + b'}'
            value = raw().replace(b'"payload":{}', b'"payload":' + payload)
            with self.subTest(duplicate_container_depth=depth), self.assertRaises(EnvelopeStructureError):
                decode_structure(value)

    def test_nonfinite_constants_overflow_and_encoder_floats_are_rejected(self):
        original = raw(payload={"v": 0})
        for constant in (b"NaN", b"Infinity", b"-Infinity", b"1e999", b"-1e999"):
            with self.subTest(constant=constant), self.assertRaises(EnvelopeStructureError):
                decode_structure(original.replace(b'"v":0', b'"v":' + constant))
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), self.assertRaises(EnvelopeStructureError):
                encode_structure(fields(payload={"v": value}))

    def test_strict_utf8_bom_utf16_utf32_and_invalid_code_units_reject(self):
        original = raw()
        invalid = (b"\xef\xbb\xbf" + original, original.decode().encode("utf-16"),
                   original.decode().encode("utf-16-le"), original.decode().encode("utf-32"),
                   original[:-1] + b'"\xff"}', raw(payload={"v": "x"}).replace(b'"v":"x"', b'"v":"\xed\xa0\x80"'))
        for value in invalid:
            with self.subTest(value=value[:12]), self.assertRaises(EnvelopeStructureError):
                decode_structure(value)

    def test_lone_surrogates_reject_but_valid_escaped_pair_is_structural(self):
        for char in ("\ud800", "\udfff"):
            bad = fields(payload={"v": char})
            self.assert_fields_invalid(bad)
            self.assert_fields_invalid(fields(payload={char: "value"}))
        escaped = raw(payload={"v": "x"}).replace(b'"v":"x"', b'"v":"\\ud83d\\ude00"')
        self.assertEqual(decode_structure(escaped).payload["v"], "😀")

    def test_raw_nul_truncation_trailing_and_packet_edge_whitespace_reject(self):
        original = raw(payload={"v": "x"})
        cases = [original[:-1], original + b"{}", original + b"junk", b"", b"null", b"[]",
                 original.replace(b'"v":"x"', b'"v":"\x00"')]
        for whitespace in (b" ", b"\t", b"\r", b"\n"):
            cases.extend((whitespace + original, original + whitespace))
        for value in cases:
            with self.subTest(value=value[:20]), self.assertRaises(EnvelopeStructureError):
                decode_structure(value)

    def test_deep_raw_container_input_rejects_as_structure_error(self):
        original = raw(payload={"v": None})
        deeply_nested = original.replace(b'"v":null', b'"v":' + b"[" * 1200 + b"0" + b"]" * 1200)
        self.assertLessEqual(len(deeply_nested), 4096)
        with self.assertRaises(EnvelopeStructureError):
            decode_structure(deeply_nested)

    def test_fields_payload_and_encoder_input_mutations_cannot_change_original_bytes(self):
        source = fields(payload={"nested": [{"value": 1}]})
        packet = encode_structure(source)
        original = packet.raw_bytes
        source["kind"] = "STOP"
        source["payload"]["nested"][0]["value"] = 99
        first_fields = packet.fields
        first_fields["payload"]["nested"][0]["value"] = 88
        first_fields["kind"] = "FAULT"
        packet.payload["nested"].append("changed copy")
        self.assertEqual(packet.raw_bytes, original)
        self.assertEqual(packet.kind, "HELLO")
        self.assertEqual(packet.payload, {"nested": [{"value": 1}]})
        self.assertIsNot(packet.fields, packet.fields)
        self.assertIsNot(packet.payload, packet.payload)
        with self.assertRaises(FrozenInstanceError):
            packet.raw_bytes = b"replaced"

    def test_direct_constructor_has_same_validation_and_owns_only_immutable_bytes(self):
        self.assertEqual(StructuralPacket(raw()).fields, fields())
        for value in (bytearray(raw()), memoryview(raw()), raw().decode(), b"{}", b"", b"\xff"):
            for entry in (decode_structure, StructuralPacket):
                with self.subTest(entry=entry, value=type(value).__name__), self.assertRaises(EnvelopeStructureError):
                    entry(value)

    def test_encoder_oversize_and_cycles_reject_before_unbounded_serialization(self):
        cyclic = {}
        cyclic["self"] = cyclic
        cyclic_list = []
        cyclic_list.append(cyclic_list)
        wide = {str(index): 0 for index in range(5000)}
        shared = [{}] * 4000
        cases = (fields(payload={"v": "x" * 1_000_000}), fields(payload={"v": "\x00" * 1000}),
                 fields(payload={"v": "😀" * 1000}), fields(payload={"v": [0] * 100_000}),
                 fields(payload=wide), fields(payload={"v": shared}), fields(payload=cyclic),
                 fields(payload={"v": cyclic_list}), fields(payload={"v": nested(20)}),
                 fields(payload={"v": 1 << 1_000_000}), fields(payload={"v": -(1 << 1_000_000)}),
                 fields(payload={"v": 1 << 13652}), fields(payload={"v": 1 << 13653}))
        for index, value in enumerate(cases):
            with self.subTest(case=index), patch("xenova_vehicle.runtime.local_envelope.json.dumps",
                    side_effect=AssertionError("serialize reached before bound")) as serializer:
                with self.assertRaises(EnvelopeStructureError):
                    encode_structure(value)
                serializer.assert_not_called()

    def test_encoder_unsupported_python_types_do_not_run_user_iteration_or_conversion(self):
        class Hostile:
            def __iter__(self):
                raise AssertionError("iterated untrusted object")

            def __str__(self):
                raise AssertionError("converted untrusted object")

        for item in (Hostile(), range(10**12), (1, 2), {1, 2}, b"bytes"):
            with self.subTest(item=type(item).__name__), self.assertRaises(EnvelopeStructureError):
                encode_structure(fields(payload={"v": item}))
        for bad in ([], None, {**fields(), 3: "non-string key"}):
            with self.assertRaises(EnvelopeStructureError):
                encode_structure(bad)

    def test_large_but_within_wire_integer_is_not_silently_coerced_or_rounded(self):
        value = 10**2000 + 123
        source = fields(payload={"v": value})
        encoded = encode_structure(source)
        self.assertLess(len(encoded.raw_bytes), 4096)
        self.assertEqual(encoded.payload["v"], value)
        self.assertIs(type(encoded.payload["v"]), int)


if __name__ == "__main__":
    unittest.main()
