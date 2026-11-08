"""실제 SDK 없이 runtime tensor/ownership 경계를 검사한다."""

from dataclasses import replace
from datetime import timedelta
from enum import IntEnum
from types import SimpleNamespace
import math
import struct
import unittest

from xenova_vehicle.oak.yolop_frames import (
    LAYER_SHAPES, MAX_NN_BYTES, NNInputStamp, NNPacket, RuntimeTensor,
    VALIDATION_ELEMENTS, VALIDATION_WORKSPACE_BYTES, _contains_nonfinite_fp16_le,
    sdk_nn_input, sdk_nn_packet, validate_nn_packet,
)


def tensors(*, flat=False, gap=0):
    offset = 0
    result = []
    for name, shape in LAYER_SHAPES.items():
        count = 1
        for dimension in shape:
            count *= dimension
        dims = (count,) if flat else shape
        strides = []
        stride = 2
        for dimension in reversed(dims):
            strides.append(stride)
            stride *= dimension
        result.append(RuntimeTensor(name, 0, 0x4321 if flat or len(shape) == 4 else 0x321,
                                    len(dims), dims, tuple(reversed(strides)), offset))
        offset += count * 2 + (gap if name == "det_out" else 0)
    return tuple(result)


def packet(*, ts=None, payload=None, **changes):
    ts = tensors() if ts is None else ts
    if payload is None:
        payload = bytes(max(t.offset + t.byte_length for t in ts))
    kwargs = dict(stream="nn_output", sequence=1, device_us=900_000, sdk_us=1_000_000,
                  received_j_us=1_100_000, payload=payload, tensors=ts, context_key="epoch-1")
    kwargs.update(changes)
    return NNPacket(**kwargs)


class Enum(IntEnum):
    FP16 = 0
    CHW = 0x321
    NCHW = 0x4321
    BGR888p = 8


class FakeNN:
    def __init__(self, ts=None, payload=None):
        ts = tensors() if ts is None else ts
        self.raw = SimpleNamespace(tensors=[SimpleNamespace(
            name=t.name, dataType=Enum.FP16, order=Enum(t.order_code),
            numDimensions=t.num_dimensions, dims=list(t.dims), strides=list(t.strides),
            offset=t.offset) for t in ts])
        self.data = bytearray(max(t.offset + t.byte_length for t in ts)) if payload is None else payload

    def getRaw(self):
        return self.raw

    def getData(self):
        return self.data

    def getSequenceNum(self):
        return 1

    def getTimestampDevice(self):
        return timedelta(microseconds=900_000)

    def getTimestamp(self):
        return timedelta(microseconds=1_000_000)

    def getLayerFp16(self, name):
        raise AssertionError("golden native accessor is adapter-only")


class FakeInput(FakeNN):
    def __init__(self):
        self.data = bytearray()

    def getType(self):
        return Enum.BGR888p

    def getWidth(self):
        return 320

    def getHeight(self):
        return 320


class YolopFrameTests(unittest.TestCase):
    def test_exact_runtime_shapes_offsets_and_finite_counter(self):
        result = packet()
        self.assertEqual(result.validation_elements, 447400)
        self.assertEqual(result.validation_elements, VALIDATION_ELEMENTS)
        self.assertEqual(len(result.payload), 894800)
        self.assertEqual(len(result.layer_bytes("det_out")), 75600)
        self.assertGreaterEqual(result.validation_elapsed_us, 0)
        self.assertFalse(result.descriptor()["compiler_layout_used"])
        self.assertEqual(result.descriptor()["validation_workspace_bound_bytes"], 409600)
        self.assertEqual(VALIDATION_WORKSPACE_BYTES, 409600)
        with self.assertRaises(KeyError):
            result.layer_bytes("unknown")

    def test_every_fp16_bit_pattern_agrees_with_independent_struct_golden(self):
        nonfinite = 0
        for bits in range(65536):
            encoded = struct.pack("<H", bits)
            expected = not math.isfinite(struct.unpack("<e", encoded)[0])
            # 양쪽 NaN padding은 활성 구간 밖이므로 읽으면 안 된다.
            actual = _contains_nonfinite_fp16_le(b"\xff\xff" + encoded + b"\xff\xff", 2, 2)
            if actual != expected:
                self.fail(f"FP16LE classification differs for bits={bits:#06x}")
            nonfinite += actual
        self.assertEqual(nonfinite, 2048)

    def test_flat_sdk_serializer_layout_is_accepted_and_preserved(self):
        result = packet(ts=tensors(flat=True))
        self.assertEqual(result.descriptor()["tensors"][0]["dims"], [37800])
        self.assertEqual(result.descriptor()["tensors"][0]["strides"], [2])
        validate_nn_packet(result)

    def test_runtime_inter_tensor_gap_is_not_compiler_assumption(self):
        ts = tensors(gap=48)
        data = bytearray(ts[-1].offset + ts[-1].byte_length)
        data[75600:75648] = b"\xff" * 48
        result = packet(ts=ts, payload=bytes(data))
        self.assertEqual(result.tensors[1].offset, 75648)
        self.assertEqual(result.validation_elements, 447400)

    def test_nonfinite_active_fp16_is_rejected_in_every_layer(self):
        ts = tensors()
        for tensor in ts:
            for bits in (0x7C00, 0xFC00, 0x7E00, 0xFFFF):
                with self.subTest(layer=tensor.name, bits=bits):
                    data = bytearray(894800)
                    struct.pack_into("<H", data, tensor.offset + tensor.byte_length - 2, bits)
                    with self.assertRaisesRegex(ValueError, "non-finite"):
                        packet(ts=ts, payload=bytes(data))

    def test_finite_extremes_and_subnormals_are_valid(self):
        data = bytearray(894800)
        for n, bits in enumerate((0, 0x8000, 1, 0x8001, 0x7BFF, 0xFBFF)):
            struct.pack_into("<H", data, n * 2, bits)
        packet(payload=bytes(data))

    def test_layer_names_count_dtype_and_rank_are_not_guessed(self):
        ts = tensors()
        bad = (ts[:2], (ts[0], ts[0], ts[2]),
               (replace(ts[0], name="other"), *ts[1:]))
        for item in bad:
            with self.subTest(tensors=item):
                with self.assertRaises(ValueError):
                    packet(ts=item, payload=bytes(894800))
        for changes in ({"data_type_code": 3}, {"num_dimensions": 4},
                        {"dims": (1, 6300, 5)}, {"order_code": 0x4321},
                        {"offset": 1}, {"strides": ()}, {"strides": (2,)},
                        {"dims": (6, 6300, 1)}, {"strides": (75648, 12, 2)}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    changed = replace(ts[0], **changes)
                    packet(ts=(changed, *ts[1:]), payload=bytes(894800))

    def test_overlap_truncation_nonowned_and_oversize_are_rejected(self):
        ts = tensors()
        for item, payload in ((ts, bytes(894799)),
                              ((ts[0], replace(ts[1], offset=0), ts[2]), bytes(894800)),
                              (ts, bytearray(894800)), (ts, bytes(MAX_NN_BYTES + 1))):
            with self.subTest(length=len(payload)):
                with self.assertRaises(ValueError):
                    packet(ts=item, payload=payload)

    def test_stamps_and_context_do_not_accept_bool_or_epoch_fallback(self):
        for changes in ({"sequence": True}, {"sdk_us": -1}, {"device_us": 2**64},
                        {"received_j_us": 1.5}, {"context_key": ""},
                        {"context_key": "has space"}, {"stream": "rgb_video"}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    packet(**changes)

    def test_sdk_factory_copies_buffer_and_primitive_metadata(self):
        message = FakeNN()
        result = sdk_nn_packet(message, 1_100_000, "epoch-1")
        message.data[0] = 255
        message.raw.tensors[0].dims[0] = 99
        self.assertEqual(result.payload[0], 0)
        self.assertEqual(result.tensors[0].dims, (1, 6300, 6))
        self.assertEqual((result.device_us, result.sdk_us), (900000, 1000000))
        self.assertEqual(result.tensors[0].data_type_code, 0)
        self.assertEqual(result.tensors[0].order_code, 0x321)

    def test_sdk_factory_rejects_malformed_raw_metadata_before_native_golden(self):
        for mutate in (lambda m: setattr(m.raw.tensors[0], "strides", []),
                       lambda m: setattr(m.raw.tensors[0], "dims", [1] * 100),
                       lambda m: setattr(m.raw.tensors[0], "dataType", 0.5),
                       lambda m: setattr(m.raw.tensors[0], "order", "801"),
                       lambda m: setattr(m, "data", memoryview(bytearray(894800))[::2]),
                       lambda m: setattr(m, "data", bytearray(MAX_NN_BYTES + 1)),
                       lambda m: setattr(m.raw, "tensors", m.raw.tensors[:2])):
            message = FakeNN()
            mutate(message)
            with self.assertRaises(ValueError):
                sdk_nn_packet(message, 1_100_000, "epoch-1")

    def test_sdk_input_metadata_only_and_noarg_end_stamps(self):
        result = sdk_nn_input(FakeInput(), 1_100_000, "epoch-1")
        self.assertIsInstance(result, NNInputStamp)
        self.assertEqual((result.width, result.height, result.encoding), (320, 320, "BGR888p"))
        self.assertEqual((result.device_us, result.sdk_us), (900000, 1000000))
        self.assertEqual(result.descriptor()["payload_length"], 0)
        for mutate in (lambda m: setattr(m, "data", bytearray(1)),
                       lambda m: setattr(m, "getType", lambda: 7),
                       lambda m: setattr(m, "getWidth", lambda: 640)):
            message = FakeInput()
            mutate(message)
            with self.assertRaises(ValueError):
                sdk_nn_input(message, 1_100_000, "epoch-1")

    def test_golden_is_bounded_json_diagnostic_separate_from_layout(self):
        original = {"matched": True, "elapsed_us": 123}
        result = packet(golden=original)
        original["matched"] = False
        self.assertTrue(result.descriptor()["golden"]["matched"])
        with self.assertRaises(ValueError):
            packet(golden={"bad": float("nan")})
        with self.assertRaises(ValueError):
            packet(golden={"bad": "x" * 8193})


if __name__ == "__main__":
    unittest.main()
