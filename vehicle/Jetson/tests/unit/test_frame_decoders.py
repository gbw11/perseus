"""배열 계약·LE depth·lazy import를 검사한다. 실제 색상 수치는 usable cv2가 있을 때만 검사한다."""

from dataclasses import replace
import gc
import os
import struct
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import weakref

from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.perception.decoders import decode_depth, decode_rgb

try:
    import numpy as np
except ImportError:
    np = None

try:
    import cv2
    _CV_READY = callable(getattr(cv2, "cvtColor", None)) and hasattr(cv2, "COLOR_YUV2BGR_NV12")
except ImportError:
    cv2 = None
    _CV_READY = False


def rgb_frame():
    # 2x2 chroma 블록 네 개: red, blue, green, neutral. NV12 UV 교차와 BGR 순서를 구별한다.
    y = bytes([81, 81, 41, 41, 81, 81, 41, 41,
               145, 145, 126, 126, 145, 145, 126, 126])
    uv = bytes([90, 240, 240, 110, 54, 34, 128, 128])
    return RawFrame("rgb_video", 123, 456, 789, 1000, 4, 4, "NV12", y + uv, 25000)


def depth_frame():
    values = (0, 1, 0x0102, 0x1234, 0x8000, 0xFFFF, 0, 1000)
    return RawFrame("depth_right", 321, 654, 987, 2000, 4, 2, "RAW16_LE",
                    struct.pack("<8H", *values), 15000)


class FrameDecoderBoundaryTests(unittest.TestCase):
    def test_import_and_invalid_input_do_not_import_native_dependencies(self):
        code = """import builtins
original=builtins.__import__
def guarded(name,*args,**kwargs):
 if name.split('.')[0] in ('numpy','cv2','depthai','torch','torchvision','vpi','paho','socket'):
  raise AssertionError('forbidden import: '+name)
 return original(name,*args,**kwargs)
builtins.__import__=guarded
from xenova_vehicle.perception.decoders import decode_rgb,decode_depth
for decode in (decode_rgb,decode_depth):
 try: decode(None)
 except ValueError: pass
 else: raise AssertionError('invalid frame accepted')
print('lazy-import PASS')
"""
        result = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True,
                                timeout=10, env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        self.assertIn("lazy-import PASS", result.stdout)

    def test_wrong_types_streams_encodings_and_endian_are_rejected_before_import(self):
        invalid = [(decode_rgb, None), (decode_depth, {}),
                   (decode_rgb, depth_frame()), (decode_depth, rgb_frame()),
                   (decode_depth, replace(depth_frame(), encoding="RAW16_BE")),
                   (decode_rgb, replace(rgb_frame(), encoding="I420")),
                   (decode_rgb, replace(rgb_frame(), encoding="NV21")),
                   (decode_rgb, replace(rgb_frame(), encoding="BGR888")),
                   (decode_rgb, replace(rgb_frame(), stream="nn_input")),
                   (decode_depth, replace(depth_frame(), payload=bytearray(16))),
                   (decode_rgb, replace(rgb_frame(), sequence=True))]
        for decoder, frame in invalid:
            with self.subTest(decoder=decoder.__name__, frame=frame):
                with self.assertRaises(ValueError):
                    decoder(frame)

    def test_short_padded_odd_rgb_and_oversize_shapes_are_rejected(self):
        invalid = [(decode_rgb, replace(rgb_frame(), payload=rgb_frame().payload[:-1])),
                   (decode_rgb, replace(rgb_frame(), payload=rgb_frame().payload + bytes(4))),
                   (decode_rgb, replace(rgb_frame(), width=3)),
                   (decode_rgb, replace(rgb_frame(), height=3)),
                   (decode_rgb, replace(rgb_frame(), width=642)),
                   (decode_depth, replace(depth_frame(), payload=bytes(15))),
                   (decode_depth, replace(depth_frame(), payload=bytes(20))),
                   (decode_depth, replace(depth_frame(), width=641)),
                   (decode_depth, replace(depth_frame(), height=481)),
                   (decode_depth, replace(depth_frame(), height=0))]
        for decoder, frame in invalid:
            with self.subTest(decoder=decoder.__name__, width=frame.width, height=frame.height,
                              length=len(frame.payload)), self.assertRaises(ValueError):
                decoder(frame)


@unittest.skipIf(np is None, "NumPy is unavailable; no numeric decoder claim on this interpreter")
class DepthDecoderTests(unittest.TestCase):
    def test_little_endian_packed_readonly_view_and_measurement_presence(self):
        frame = depth_frame()
        decoded = decode_depth(frame)
        self.assertIs(decoded.frame, frame)
        self.assertEqual(decoded.frame.descriptor(), frame.descriptor())
        self.assertEqual(decoded.depth_mm.dtype.str, "<u2")
        self.assertEqual(decoded.depth_mm.shape, (2, 4))
        self.assertEqual(decoded.depth_mm.strides, (8, 2))
        self.assertEqual(decoded.depth_mm.strides[0], frame.descriptor()["row_stride_bytes"])
        self.assertTrue(decoded.depth_mm.flags.c_contiguous)
        self.assertFalse(decoded.depth_mm.flags.owndata)
        self.assertFalse(decoded.depth_mm.flags.writeable)
        self.assertTrue(np.shares_memory(decoded.depth_mm, np.frombuffer(frame.payload, dtype="<u2")))
        np.testing.assert_array_equal(decoded.depth_mm, [[0, 1, 258, 4660], [32768, 65535, 0, 1000]])
        np.testing.assert_array_equal(decoded.has_measurement, [[False, True, True, True],
                                                              [True, True, False, True]])
        self.assertEqual(decoded.has_measurement.dtype, np.dtype(bool))
        self.assertTrue(decoded.has_measurement.flags.owndata)
        self.assertFalse(decoded.has_measurement.flags.writeable)
        self.assertEqual(decoded.depth_unit, "millimeter")
        self.assertEqual(decoded.alignment, "RECTIFIED_RIGHT")
        self.assertFalse(decoded.rgb_aligned or decoded.metric_ready)
        with self.assertRaises(ValueError):
            decoded.depth_mm[0, 0] = 1
        with self.assertRaises(ValueError):
            decoded.has_measurement[0, 0] = True

    def test_frame_and_bytes_view_have_independent_strong_lifetimes(self):
        frame = depth_frame()
        original = weakref.ref(frame)
        decoded = decode_depth(frame)
        view = decoded.depth_mm
        del frame
        gc.collect()
        self.assertIs(original(), decoded.frame)
        del decoded
        gc.collect()
        self.assertIsNone(original())
        self.assertEqual(view[0, 3], 0x1234)
        self.assertFalse(view.flags.writeable)

    def test_maximum_native_frame_keeps_original_source_time(self):
        frame = replace(depth_frame(), width=640, height=480, payload=bytes(640 * 480 * 2))
        decoded = decode_depth(frame)
        self.assertIs(decoded.frame, frame)
        self.assertEqual(decoded.depth_mm.shape, (480, 640))
        self.assertEqual(decoded.depth_mm.strides, (1280, 2))
        self.assertFalse(decoded.has_measurement.any())
        self.assertEqual((decoded.frame.sequence, decoded.frame.device_us, decoded.frame.sdk_us,
                          decoded.frame.received_j_us, decoded.frame.exposure_us),
                         (321, 654, 987, 2000, 15000))

    def test_depth_decode_never_imports_opencv(self):
        original = __import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in ("cv2", "depthai"):
                raise AssertionError("depth decode imported " + name)
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded):
            self.assertEqual(decode_depth(depth_frame()).depth_mm[0, 2], 258)


@unittest.skipIf(np is None, "NumPy is unavailable; RGB stub checks need arrays")
class RgbDecoderCallContractTests(unittest.TestCase):
    def test_one_nv12_conversion_populates_owned_readonly_bgr_and_preserves_frame(self):
        frame = rgb_frame()
        calls = []
        expected = np.arange(4 * 4 * 3, dtype=np.uint8).reshape(4, 4, 3)

        def convert(source, code, *, dst):
            calls.append((source, code, dst))
            self.assertEqual(source.shape, (6, 4))
            self.assertEqual(source.strides, (4, 1))
            self.assertEqual(source.dtype, np.dtype(np.uint8))
            self.assertFalse(source.flags.writeable)
            self.assertEqual(code, "NV12_TO_BGR_STUB")
            np.copyto(dst, expected)
            return dst

        fake = SimpleNamespace(cvtColor=convert, COLOR_YUV2BGR_NV12="NV12_TO_BGR_STUB")
        with patch.dict(sys.modules, {"cv2": fake}):
            decoded = decode_rgb(frame)
        self.assertEqual(len(calls), 1)  # 이 시험은 호출/소유 계약만 검증하며 색상 수치 시험이 아니다.
        self.assertIs(decoded.frame, frame)
        self.assertEqual(decoded.frame.descriptor(), frame.descriptor())
        self.assertEqual(decoded.bgr.dtype, np.dtype(np.uint8))
        self.assertEqual(decoded.bgr.shape, (4, 4, 3))
        self.assertEqual(decoded.bgr.strides, (12, 3, 1))
        self.assertTrue(decoded.bgr.flags.c_contiguous and decoded.bgr.flags.owndata)
        self.assertFalse(decoded.bgr.flags.writeable)
        self.assertFalse(np.shares_memory(decoded.bgr, np.frombuffer(frame.payload, dtype=np.uint8)))
        np.testing.assert_array_equal(decoded.bgr, expected)
        with self.assertRaises(ValueError):
            decoded.bgr[0, 0, 0] = 1

    def test_conversion_that_ignores_owned_destination_is_rejected(self):
        def wrong(_source, _code, *, dst):
            return np.zeros_like(dst)

        fake = SimpleNamespace(cvtColor=wrong, COLOR_YUV2BGR_NV12="NV12_TO_BGR_STUB")
        with patch.dict(sys.modules, {"cv2": fake}), self.assertRaises(ValueError):
            decode_rgb(rgb_frame())


@unittest.skipUnless(np is not None and _CV_READY,
                     "usable OpenCV cvtColor is unavailable; known-color numeric golden not executed")
class RgbDecoderNumericTests(unittest.TestCase):
    def test_known_nv12_uv_order_bgr_channels_and_hwc_shape(self):
        frame = rgb_frame()
        decoded = decode_rgb(frame)
        # 독립 limited-range BT.601 known colors. OpenCV fixed-point 반올림은 ±1 허용한다.
        colors = np.array([[[0, 0, 254], [255, 0, 0]],
                           [[1, 255, 0], [128, 128, 128]]], dtype=np.uint8)
        expected = np.repeat(np.repeat(colors, 2, axis=0), 2, axis=1)
        np.testing.assert_allclose(decoded.bgr, expected, atol=1, rtol=0)
        packed = np.frombuffer(frame.payload, dtype=np.uint8).reshape(6, 4)
        self.assertFalse(np.array_equal(decoded.bgr, cv2.cvtColor(packed, cv2.COLOR_YUV2BGR_I420)))
        self.assertFalse(np.array_equal(decoded.bgr, cv2.cvtColor(packed, cv2.COLOR_YUV2BGR_NV21)))

    def test_luma_black_and_white_and_exactly_one_real_conversion(self):
        frame = RawFrame("rgb_video", 1, 2, 3, 4, 2, 2, "NV12", bytes([16, 235, 16, 235, 128, 128]))
        with patch.object(cv2, "cvtColor", wraps=cv2.cvtColor) as convert:
            decoded = decode_rgb(frame)
        self.assertEqual(convert.call_count, 1)
        np.testing.assert_array_equal(decoded.bgr, [[[0, 0, 0], [255, 255, 255]],
                                                   [[0, 0, 0], [255, 255, 255]]])


if __name__ == "__main__":
    unittest.main()
