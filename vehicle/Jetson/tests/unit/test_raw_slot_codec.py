"""합성 P0 owned-slot의 byte·layout·lexical 경계. 실제 FILLED/SHM/NN READY 증명은 아니다."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import struct
import unittest
from unittest.mock import patch
from uuid import UUID
import zlib

from raw_lease_fixtures import identifier
from raw_slot_fixtures import bundle, context, descriptor, expected, payload, raw_record
from xenova_vehicle.runtime.raw_slot import (
    ExpectedSlot, OwnedSlot, P0Context, PrefixHeader, SlotCodecError, SlotEncoding,
    decode_owned, encode_owned, prefix,
)


_HEADER = struct.Struct("<8sHHI16sQ16s32sIII28s")
_HEADER_FIELDS = {"magic": 0, "version": 1, "header_bytes": 2, "descriptor_length": 3,
                  "arena": 4, "generation": 5, "write_token": 6, "raw_sha": 7,
                  "payload_length": 8, "descriptor_crc": 9, "payload_crc": 10, "reserved": 11}


def _change_header(raw, **changes):
    """제품 parser 없이 명문 offset의 필드를 바꾼다. CRC 자동 수리는 하지 않는다."""
    values = list(_HEADER.unpack(raw[:128]))
    for name, value in changes.items():
        values[_HEADER_FIELDS[name]] = value
    return _HEADER.pack(*values) + raw[128:]


def _json(desc):
    return json.dumps(desc, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")


class RawSlotCodecTests(unittest.TestCase):
    def assert_decode_bad(self, raw, wanted, supplied_prefix=None):
        for entry in (decode_owned, OwnedSlot):
            with self.subTest(entry=entry.__name__), self.assertRaises(SlotCodecError):
                entry(raw, wanted, supplied_prefix)

    def assert_descriptor_bad(self, desc, *, ctx=None, body=None):
        # 잘못된 offset/shape로 fixture 할당을 유도하지 않고 정상 bounded body를 쓴다.
        ctx = context() if ctx is None else ctx
        body = payload(descriptor()) if body is None else body
        raw, wanted = raw_record(ctx, desc, body)
        self.assert_decode_bad(raw, wanted)
        for entry in (encode_owned, SlotEncoding):
            with self.subTest(entry=entry.__name__), self.assertRaises(SlotCodecError):
                entry(ctx, desc, body)

    def test_independent_record_roundtrip_and_compact_encoding_are_owned_bytes_only(self):
        ctx, desc, body = bundle()
        original, wanted = raw_record(ctx, desc, body)
        decoded = decode_owned(original, wanted)
        encoded = encode_owned(ctx, desc, body)
        self.assertEqual(encoded.raw, original)
        self.assertEqual(decoded.raw, original)
        self.assertEqual(decoded.descriptor(), desc)
        self.assertEqual(encoded.descriptor_bytes, _json(desc))
        self.assertEqual(encoded.descriptor_sha256, hashlib.sha256(_json(desc)).hexdigest())
        self.assertEqual(encoded.payload_length, len(body))
        self.assertEqual(decoded.payload_bytes(), body)
        self.assertEqual(decoded.expected, wanted)
        self.assertIs(type(decoded.raw), bytes)
        self.assertFalse(any(hasattr(decoded, name) for name in (
            "nn_ready", "metric_ready", "authorized", "actual_exit", "shm", "nn_packet")))

    def test_header_golden_offsets_little_endian_and_rfc_uuid_bytes(self):
        ctx, desc, body = bundle()
        unusual = "01234567-89ab-cdef-8123-456789abcdef"
        scope = replace(ctx.write_key.scope, arena_id=unusual)
        key = replace(ctx.write_key, scope=scope, generation=0x0102030405060708,
                      write_token="76543210-ab89-efcd-9123-456789abcdef")
        ctx = P0Context(key, dict(ctx.stream_ids), True)
        raw = encode_owned(ctx, desc, body).raw
        self.assertEqual(raw[:8], b"XNVRAW1\x00")
        self.assertEqual(raw[8:12], b"\x01\x00\x80\x00")
        self.assertEqual(raw[12:16], len(_json(desc)).to_bytes(4, "little"))
        self.assertEqual(raw[16:32], bytes.fromhex("0123456789abcdef8123456789abcdef"))
        self.assertNotEqual(raw[16:32], UUID(unusual).bytes_le)
        self.assertEqual(raw[32:40], bytes.fromhex("0807060504030201"))
        self.assertEqual(raw[40:56], UUID(key.write_token).bytes)
        self.assertEqual(raw[56:88], bytes.fromhex("aa" * 32))
        self.assertEqual(raw[88:92], len(body).to_bytes(4, "little"))
        self.assertEqual(raw[100:128], bytes(28))
        self.assertEqual(_HEADER.size, 128)
        self.assertEqual(zlib.crc32(b"123456789", 0), 0xcbf43926)

    def test_prefix_exact_length_fixed_fields_and_bounded_unsigned_header(self):
        ctx, desc, body = bundle()
        raw, _ = raw_record(ctx, desc, body)
        for entry in (prefix, PrefixHeader):
            result = entry(raw[:128], ctx)
            self.assertEqual(result.raw, raw[:128])
            self.assertEqual(result.context, ctx)
            self.assertEqual(result.used_length, 4096 + len(body))
            for short_or_extra in (raw[:127], raw[:129], b"", bytearray(raw[:128]), memoryview(raw[:128])):
                with self.assertRaises(SlotCodecError):
                    entry(short_or_extra, ctx)
            for changes in ({"magic": b"XNVRAW2\0"}, {"version": 0}, {"header_bytes": 129},
                            {"descriptor_length": 0}, {"descriptor_length": 3969},
                            {"generation": 0}, {"payload_length": 0}, {"payload_length": 2093057},
                            {"reserved": b"\x01" + bytes(27)}):
                with self.subTest(changes=tuple(changes)), self.assertRaises(SlotCodecError):
                    entry(_change_header(raw, **changes)[:128], ctx)
        # prefix의 작은 L은 아직 P0 plane 검사가 아니므로1도 header 형식으로는 유효하다.
        self.assertEqual(prefix(_change_header(raw, payload_length=1)[:128], ctx).payload_length, 1)

    def test_header_identity_changes_do_not_hide_behind_unchanged_body_crcs(self):
        ctx, desc, body = bundle()
        raw, wanted = raw_record(ctx, desc, body)
        variants = ({"arena": UUID(identifier(900)).bytes}, {"generation": 2},
                    {"write_token": UUID(identifier(901)).bytes}, {"raw_sha": bytes(32)})
        for changes in variants:
            broken = _change_header(raw, **changes)
            self.assert_decode_bad(broken, wanted)
            with self.assertRaises(SlotCodecError):
                prefix(broken[:128], ctx)

    def test_external_filled_context_requires_full_write_key_even_unencoded_fields(self):
        ctx, desc, body = bundle()
        raw, wanted = raw_record(ctx, desc, body)
        key = ctx.write_key
        alternatives = (replace(key, slot=1),
                        replace(key, scope=replace(key.scope, service_instance=identifier(901))),
                        replace(key, scope=replace(key.scope, writer=replace(key.scope.writer, channel_epoch=identifier(902)))),
                        replace(key, scope=replace(key.scope, writer=replace(key.scope.writer, worker_instance=identifier(903)))))
        for changed in alternatives:
            foreign_context = P0Context(changed, dict(ctx.stream_ids), True)
            with self.assertRaises(SlotCodecError):
                ExpectedSlot(foreign_context, wanted.original_filled_info)
        info = wanted.original_filled_info
        for changed in (replace(info, payload_length=len(body) - 1),
                        replace(info, descriptor_sha256="f" * 64)):
            self.assert_decode_bad(raw, ExpectedSlot(ctx, changed))

    def test_descriptor_crc_recalculation_cannot_replace_original_filled_sha(self):
        ctx, desc, body = bundle()
        raw, wanted = raw_record(ctx, desc, body)
        # 의미는 같은 ASCII escape JSON도 원 descriptor bytes/SHA는 다른 content다.
        escaped = _json(desc).replace(b'"version"', b'"ver\\u0073ion"', 1)
        changed, new_expected = raw_record(ctx, desc, body, descriptor_raw=escaped)
        self.assert_decode_bad(changed, wanted)
        self.assertEqual(decode_owned(changed, new_expected).descriptor(), desc)
        self.assertEqual(decode_owned(changed, new_expected).descriptor_bytes, escaped)
        self.assertNotEqual(raw, changed)

    def test_crc_spans_cover_original_descriptor_and_full_body_but_not_metadata_slack(self):
        ctx, desc, body = bundle()
        raw, wanted = raw_record(ctx, desc, body)
        header = prefix(raw[:128], ctx)
        self.assertEqual(header.descriptor_crc32, zlib.crc32(raw[128:128 + header.descriptor_length], 0))
        self.assertEqual(header.payload_crc32, zlib.crc32(raw[4096:], 0))
        for index in (128, 4096, len(raw) - 1):
            changed = bytearray(raw)
            changed[index] ^= 1
            self.assert_decode_bad(bytes(changed), wanted)
        slack = bytearray(raw)
        slack[128 + header.descriptor_length] = 1
        # slack은 두CRC 밖이어도 zero 규약 자체를 검사한다.
        self.assert_decode_bad(bytes(slack), wanted)
        for name in ("descriptor_crc", "payload_crc"):
            self.assert_decode_bad(_change_header(raw, **{name: 0}), wanted)

    def test_exact_3968_descriptor_whitespace_is_valid_original_span_3969_is_rejected(self):
        ctx, desc, body = bundle()
        compact = _json(desc)
        self.assertLess(len(compact), 3968)
        padding = 3968 - len(compact)
        wire = b" " * (padding // 2) + compact + b"\n" * (padding - padding // 2)
        raw, wanted = raw_record(ctx, desc, body, descriptor_raw=wire)
        for entry in (decode_owned, OwnedSlot):
            decoded = entry(raw, wanted)
            self.assertEqual(decoded.descriptor_bytes, wire)
            self.assertEqual(decoded.header.descriptor_length, 3968)
            self.assertEqual(decoded.descriptor_sha256, hashlib.sha256(wire).hexdigest())
            self.assertEqual(decoded.descriptor(), desc)
        too_long, excessive = raw_record(ctx, desc, body, descriptor_raw=wire + b" ")
        self.assert_decode_bad(too_long, excessive)

    def test_exact_used_slot_capacity_and_slot_relative_layout_are_valid(self):
        ctx, desc, _ = bundle()
        desc["planes"]["nn_output0"].update(offset=1048576, length=1048576, shape=[1048576])
        body = payload(desc)
        self.assertEqual(len(body), 2093056)
        raw, wanted = raw_record(ctx, desc, body)
        self.assertEqual(len(raw), 2097152)
        self.assertEqual(decode_owned(raw, wanted).raw, raw)
        self.assertEqual(encode_owned(ctx, desc, body).raw, raw)
        self.assert_decode_bad(raw + b"\0", wanted)
        # slot1에서도 descriptor의 O에 arena base2MiB를 미리 더하지 않는다.
        second = context(slot=1)
        second_raw, second_wanted = raw_record(second, desc, body)
        self.assertEqual(decode_owned(second_raw, second_wanted).descriptor()["planes"]["rgb_video"]["offset"], 4096)

    def test_short_extra_or_full_unused_slot_tail_is_not_silently_sliced_or_stripped(self):
        ctx, desc, body = bundle()
        raw, wanted = raw_record(ctx, desc, body)
        for changed in (raw[:-1], raw + b"\0", raw + bytes(2097152 - len(raw))):
            self.assert_decode_bad(changed, wanted)
        for bad in (bytearray(raw), memoryview(raw), None):
            self.assert_decode_bad(bad, wanted)
        for entry in (encode_owned, SlotEncoding):
            for bad in (bytearray(body), memoryview(body), body[:-1], body + b"\0"):
                with self.assertRaises(SlotCodecError):
                    entry(ctx, desc, bad)

    def test_layout_failures_recompute_crc_and_external_sha_to_reach_semantic_checks(self):
        mutations = (
            ("rgb_video", "offset", 4095), ("rgb_video", "offset", 4097),
            ("rgb_video", "length", 345599), ("rgb_video", "shape", [360, 960]),
            ("rgb_video", "strides_bytes", [1, 640]), ("rgb_video", "dtype", "<u2"),
            ("rgb_video", "encoding", "BGR888p"), ("depth_right", "offset", 4096),
            ("depth_right", "offset", 2097152), ("depth_right", "strides_bytes", [640, 1]),
            ("depth_right", "shape", [640, 480]), ("depth_right", "dtype", "u2"),
            ("depth_right", "length", 614401), ("nn_output0", "length", 0),
            ("nn_output0", "length", 1048577), ("nn_output0", "shape", [1, 894848]),
            ("nn_output0", "strides_bytes", [2]), ("nn_output0", "dtype", "<f2"),
            ("nn_output0", "offset", 10**12), ("rgb_video", "offset", True),
            ("depth_right", "length", 614400.0), ("depth_right", "shape", [True, 640]),
        )
        for plane, field, replacement in mutations:
            desc = descriptor()
            desc["planes"][plane][field] = replacement
            with self.subTest(plane=plane, field=field, replacement_type=type(replacement).__name__):
                self.assert_descriptor_bad(desc)

    def test_plane_gap_original_bytes_are_crc_covered_and_preserved_without_zero_repair(self):
        ctx, desc, _ = bundle(nn_present=False)
        desc["planes"]["depth_right"]["offset"] += 64
        original = bytearray(payload(desc))
        original[345600:345664] = bytes(range(64))
        body = bytes(original)
        encoded = encode_owned(ctx, desc, body)
        self.assertEqual(encoded.payload_bytes(), body)
        raw, wanted = raw_record(ctx, desc, body)
        changed = bytearray(raw)
        changed[4096 + 345600] ^= 1
        self.assert_decode_bad(bytes(changed), wanted)
        fixed = _change_header(bytes(changed), payload_crc=zlib.crc32(changed[4096:], 0))
        self.assertEqual(decode_owned(fixed, wanted).payload_bytes(), bytes(changed[4096:]))

    def test_nn_runtime_storage_relative_offsets_order_and_non64_alignment_are_preserved(self):
        ctx, desc, body = bundle()
        desc["nn"]["layers"].reverse()
        # 세 tensor 모두2B 이동한다. plane만64B 정렬이고 tensor는2B면 된다.
        for layer in desc["nn"]["layers"]:
            layer["offset"] += 2
        desc["planes"]["nn_output0"].update(length=894850, shape=[894850])
        body = payload(desc)
        raw, wanted = raw_record(ctx, desc, body)
        decoded = decode_owned(raw, wanted)
        self.assertEqual(decoded.descriptor()["nn"]["layers"], desc["nn"]["layers"])
        self.assertEqual(encode_owned(ctx, desc, body).raw, raw)

    def test_nn_metadata_exact_three_packed_layers_and_bounds_are_independently_validated(self):
        edits = (("offset", 1), ("offset", 2**63), ("length", 75598),
                 ("data_type_code", True), ("data_type_code", 1), ("order_code", 17185),
                 ("dims", [37800]), ("dims", [1, 6, 6300]),
                 ("strides_bytes", []), ("strides_bytes", [75600, 2, 12]))
        for field, replacement in edits:
            desc = descriptor()
            desc["nn"]["layers"][0][field] = replacement
            self.assert_descriptor_bad(desc)
        for mode in ("overlap", "duplicate_name", "missing", "four", "unknown_name", "short_storage"):
            desc = descriptor()
            layers = desc["nn"]["layers"]
            if mode == "overlap":
                layers[1]["offset"] = 0
            elif mode == "duplicate_name":
                layers[1]["name"] = "det_out"
            elif mode == "missing":
                layers.pop()
            elif mode == "four":
                layers.append(layers[0].copy())
            elif mode == "unknown_name":
                layers[0]["name"] = "other"
            else:
                desc["planes"]["nn_output0"].update(length=894847, shape=[894847])
            with self.subTest(mode=mode):
                self.assert_descriptor_bad(desc)

    def test_nan_in_active_nn_and_original_padding_is_structural_codec_success_only(self):
        ctx, desc, body = bundle()
        storage_start = desc["planes"]["nn_output0"]["offset"] - 4096
        changed = bytearray(body)
        changed[storage_start:storage_start + 2] = b"\x00\x7e"  # 실제 FP16 NaN bit pattern.
        changed[storage_start + 75600:storage_start + 75648] = b"\x00\x7e" * 24
        raw, wanted = raw_record(ctx, desc, bytes(changed))
        decoded = decode_owned(raw, wanted)
        self.assertEqual(decoded.payload_bytes(), bytes(changed))
        self.assertEqual(encode_owned(ctx, desc, bytes(changed)).raw, raw)
        self.assertFalse(hasattr(decoded, "nn_ready"))

    def test_unknown_or_missing_keys_are_rejected_at_each_layout_object_level(self):
        paths = ((), ("planes",), ("planes", "rgb_video"), ("nn",), ("nn", "layers", 0))
        for path in paths:
            for mode in ("unknown", "missing"):
                desc = descriptor()
                node = desc
                for key in path:
                    node = node[key]
                if mode == "unknown":
                    node["unexpected"] = None
                else:
                    del node[next(iter(node))]
                with self.subTest(path=path, mode=mode):
                    self.assert_descriptor_bad(desc)

    def test_raw_ascii_escape_whitespace_and_encoder_canonical_subset_have_distinct_bytes(self):
        ctx, desc, body = bundle()
        wire = b"\t\r\n " + _json(desc).replace(b'"version"', b'"ver\\u0073ion"', 1) + b" \n"
        raw, wanted = raw_record(ctx, desc, body, descriptor_raw=wire)
        decoded = decode_owned(raw, wanted)
        self.assertEqual(decoded.descriptor(), desc)
        self.assertEqual(decoded.descriptor_bytes, wire)
        self.assertEqual(decoded.raw, raw)
        self.assertEqual(encode_owned(ctx, decoded.descriptor(), body).descriptor_bytes, _json(desc))

    def test_bomless_utf16_and_utf32_with_ascii_bytes_are_not_ascii_json(self):
        ctx, desc, body = bundle(nn_present=False)
        text = _json(desc).decode("ascii")
        for encoding in ("utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"):
            wire = text.encode(encoding)
            with self.subTest(encoding=encoding):
                self.assertTrue(wire.isascii())
                self.assertIn(b"\0", wire)
                self.assertLessEqual(len(wire), 3968)
                self.assertFalse(wire.startswith((b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")))
                # BOM/상한/CRC/SHA 오류가 아닌 JSON bytes 자동 encoding 탐지를 겨냥한다.
                raw, wanted = raw_record(ctx, desc, body, descriptor_raw=wire)
                self.assert_decode_bad(raw, wanted)

    def test_nested_decoded_duplicate_keys_and_other_json_tokens_do_not_get_normalized(self):
        ctx, desc, body = bundle()
        compact = _json(desc)
        variants = (compact.replace(b'"version":1', b'"version":1,"ver\\u0073ion":1', 1),
                    compact.replace(b'"offset":4096', b'"offset":4096,"off\\u0073et":4096', 1),
                    compact.replace(b'"dims":[1,6300,6]', b'"dims":[1,6300,6],"dims":[1,6300,6]', 1),
                    compact + b"{}", b"\xef\xbb\xbf" + compact, compact[:-1],
                    compact.replace(b'"version":1', b'"version":NaN', 1),
                    compact.replace(b'"version":1', b'"version":Infinity', 1),
                    compact.replace(b'"version":1', b'"version":1.0', 1),
                    compact.replace(b'"version":1', b'"version":true', 1),
                    compact.replace(b'p0-yolop-320-v1', b'\\ud800', 1),
                    compact.replace(b'p0-yolop-320-v1', b'\\u00e9', 1),
                    compact.replace(b'p0-yolop-320-v1', "한글".encode("utf-8"), 1))
        for index, wire in enumerate(variants):
            self.assertNotEqual(wire, compact)
            raw, wanted = raw_record(ctx, desc, body, descriptor_raw=wire)
            with self.subTest(index=index):
                self.assert_decode_bad(raw, wanted)

    def test_native_tree_work_caps_reject_before_json_serialization(self):
        ctx, desc, body = bundle()
        cycle = []
        cycle.append(cycle)
        deep = None
        for _ in range(6):
            deep = [deep]
        huge = "x" * 1_000_000
        nodes = None
        for _ in range(4):
            nodes = [nodes] * 4
        bad_values = (cycle, deep, nodes, [None] * 10000, {str(i): None for i in range(9)},
                      huge, 1 << 1_000_000, float("nan"), float("inf"), True, b"raw")
        # 이 alias tree는 root 포함 depth5 안이지만 출현별 node512를 넘는다.
        with patch("json.dumps", side_effect=AssertionError("native bound must run before serializer")), \
                patch("xenova_vehicle.runtime.raw_slot._schema", side_effect=AssertionError("native bound must run before schema")):
            for index, bad in enumerate(bad_values):
                invalid = desc.copy()
                invalid["profile"] = bad
                with self.subTest(index=index):
                    for entry in (encode_owned, SlotEncoding):
                        with self.assertRaises(SlotCodecError):
                            entry(ctx, invalid, body)

    def test_native_custom_types_metaclasses_and_keys_never_run_user_hooks(self):
        ctx, desc, body = bundle()
        calls = []

        def trap(name):
            calls.append(name)
            raise AssertionError("unsupported native hook: " + name)

        class StringTrap(str):
            def __len__(self):
                return trap("str.len")

            def __eq__(self, other):
                return trap("str.eq")

            def __hash__(self):
                calls.append("str.hash")
                return str.__hash__(self)

        class DictTrap(dict):
            def __len__(self):
                return trap("dict.len")

            def items(self):
                return trap("dict.items")

        class IntegerTrap(int):
            def bit_length(self):
                return trap("int.bit_length")

        class TypeTrap(type):
            def __eq__(self, other):
                return trap("type.eq")

            def __hash__(self):
                return trap("type.hash")

        class ObjectTrap(metaclass=TypeTrap):
            def __str__(self):
                return trap("object.str")

            def __deepcopy__(self, memo):
                return trap("object.deepcopy")

        variants = [DictTrap(desc)]
        for bad in (StringTrap("p0-yolop-320-v1"), IntegerTrap(1), ObjectTrap()):
            invalid = desc.copy()
            invalid["profile"] = bad
            variants.append(invalid)
        invalid_key = desc.copy()
        invalid_key[StringTrap("extra")] = None
        variants.append(invalid_key)
        calls.clear()  # custom key의 fixture 삽입은 제품 진입 전이며 hook 분모에서 제외한다.
        for index, invalid in enumerate(variants):
            with self.subTest(index=index):
                for entry in (encode_owned, SlotEncoding):
                    with self.assertRaises(SlotCodecError):
                        entry(ctx, invalid, body)
        self.assertEqual(calls, [])

    def test_noncyclic_alias_and_normal_deepest_shape_remain_valid_owned_copies(self):
        ctx, desc, body = bundle()
        bridge = desc["streams"]["rgb_video"]["bridge_sample"]
        interval = desc["streams"]["rgb_video"]["sdk_host_j_interval_us"]
        for name in ("nn_input0", "nn_output0"):
            desc["streams"][name]["bridge_sample"] = bridge
            desc["streams"][name]["sdk_host_j_interval_us"] = interval
        desc["nn"]["layers"][2]["dims"] = desc["nn"]["layers"][1]["dims"]
        original = deepcopy(desc)
        encoded = SlotEncoding(ctx, desc, body)
        desc["streams"]["rgb_video"]["source_key"][1] = "999"
        bridge["sdk_us"] = "999"
        interval[0] = "0"
        self.assertEqual(encoded.descriptor(), original)

    def test_context_stream_map_native_copy_and_128_character_arena_name_have_separate_bounds(self):
        ctx = context()
        source = dict(ctx.stream_ids)
        key = replace(ctx.write_key, scope=replace(ctx.write_key.scope, arena_name="a" * 128))
        owned = P0Context(key, source, True)
        snapshot = owned.stream_ids
        source["rgb_video"] = identifier(900)
        with self.assertRaises(TypeError):
            snapshot["rgb_video"] = identifier(901)
        self.assertEqual(owned.stream_ids, ctx.stream_ids)
        self.assertEqual(owned.write_key.scope.arena_name, "a" * 128)
        for bad in (1, 0, None, "true"):
            with self.assertRaises(SlotCodecError):
                P0Context(key, dict(ctx.stream_ids), bad)
        for bad in ({}, {**ctx.stream_ids, "extra": identifier(901)},
                    {**ctx.stream_ids, "depth_right": ctx.stream_ids["rgb_video"]}):
            with self.assertRaises(SlotCodecError):
                P0Context(key, bad, True)

    def test_public_constructors_validate_same_inputs_and_reject_unchecked_state_injection(self):
        ctx, desc, body = bundle()
        raw, wanted = raw_record(ctx, desc, body)
        header = PrefixHeader(raw[:128], ctx)
        self.assertEqual(OwnedSlot(raw, wanted, header).raw, raw)
        self.assertEqual(SlotEncoding(ctx, desc, body).raw, raw)
        for entry, args in ((OwnedSlot, (raw, wanted)), (SlotEncoding, (ctx, desc, body)),
                            (PrefixHeader, (raw[:128], ctx))):
            for keyword in ("validated", "_validated", "_descriptor", "_header"):
                with self.subTest(entry=entry.__name__, keyword=keyword), self.assertRaises(TypeError):
                    entry(*args, **{keyword: True})
        for invalid in (None, {"context": ctx}, ctx):
            self.assert_decode_bad(raw, invalid)
        with self.assertRaises(SlotCodecError):
            ExpectedSlot(ctx, {"payload_length": len(body)})

    def test_optional_prefix_requires_identical_final_header_not_just_same_identity(self):
        ctx, desc, body = bundle()
        raw, wanted = raw_record(ctx, desc, body)
        h1 = prefix(raw[:128], ctx)
        # D만 늘고 whitespace를 추가한 H2는 독립적으로도 정상인 header/body다.
        changed, current = raw_record(ctx, desc, body, descriptor_raw=b" " + _json(desc))
        self.assertEqual(decode_owned(changed, current).descriptor(), desc)
        self.assert_decode_bad(changed, current, h1)
        matching = prefix(changed[:128], ctx)
        self.assertEqual(decode_owned(changed, current, matching).raw, changed)
        self.assert_decode_bad(raw, wanted, object())

    def test_public_nested_descriptor_copies_do_not_modify_raw_or_expected_identity(self):
        ctx, desc, body = bundle()
        encoded = encode_owned(ctx, desc, body)
        decoded = decode_owned(encoded.raw, expected(encoded))
        for value in (encoded, decoded):
            before, original = value.raw, value.descriptor()
            exported = value.descriptor()
            exported["planes"]["rgb_video"]["shape"][0] = 1
            exported["nn"]["layers"][0]["offset"] = 2
            exported["streams"]["rgb_video"]["source_key"][0] = identifier(900)
            self.assertEqual(value.descriptor(), original)
            self.assertEqual(value.raw, before)
            self.assertIsNot(value.descriptor(), value.descriptor())
            with self.assertRaises((AttributeError, TypeError)):
                value.raw = b"unchecked"


if __name__ == "__main__":
    unittest.main()
