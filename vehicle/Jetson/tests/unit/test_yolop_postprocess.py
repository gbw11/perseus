"""독립 수치 기대값과 고정 runtime bytes로 YOLOP 후보 후처리를 검사한다."""

from dataclasses import replace
import gc
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import unittest
import weakref

from xenova_vehicle.oak.yolop_frames import NNPacket, RuntimeTensor
from xenova_vehicle.perception.yolop import (
    PARSER_ID, PREPROCESSING_ID, REFERENCE_UTILS_SHA256, YolopPostprocessConfig, decode_yolop,
)


_JETSON = Path(__file__).resolve().parents[2]
_AUDIT = _JETSON / "build/implementation-supervision-20261107/your-audit"
_MASK_VALUES = 320 * 320


def packet(rows=(), *, road=(), lane=(), prefix=0, suffix=0, reverse=False, flat=False,
           road_foreground=None):
    """FP16 입력만 조립한다. 후처리·NMS·기대값 계산은 하지 않는다."""
    shapes = (("det_out", (1, 6300, 6), 75600),
              ("drive_area_seg", (1, 2, 320, 320), 409600),
              ("lane_line_seg", (1, 2, 320, 320), 409600))
    offset, tensors = prefix, []
    for name, dims, length in shapes:
        if flat:
            dims, strides, order = (length // 2,), (2,), 17185
        elif name == "det_out":
            strides, order = (75600, 12, 2), 801
        else:
            strides, order = (409600, 204800, 640, 2), 17185
        tensors.append(RuntimeTensor(name, 0, order, len(dims), dims, strides, offset))
        offset += length + (48 if name == "det_out" else 0)
    data = bytearray(b"\xff" * (offset + suffix))
    by_name = {tensor.name: tensor for tensor in tensors}
    for tensor in tensors:
        data[tensor.offset:tensor.offset + tensor.byte_length] = bytes(tensor.byte_length)
    for index, values in enumerate(rows):
        if index >= 6300 or len(values) != 6:
            raise ValueError("synthetic input requires at most6300 six-value rows")
        struct.pack_into("<6e", data, by_name["det_out"].offset + index * 12, *values)
    if road_foreground is not None:
        if len(road_foreground) != _MASK_VALUES * 2:
            raise ValueError("invalid synthetic foreground plane")
        start = by_name["drive_area_seg"].offset + _MASK_VALUES * 2
        data[start:start + _MASK_VALUES * 2] = road_foreground
    for name, updates in (("drive_area_seg", road), ("lane_line_seg", lane)):
        start = by_name[name].offset
        for index, background, foreground in updates:
            struct.pack_into("<e", data, start + index * 2, background)
            struct.pack_into("<e", data, start + (_MASK_VALUES + index) * 2, foreground)
    return NNPacket("nn_output", 10, 900000, 1000000, 1100000, bytes(data),
                    tuple(reversed(tensors)) if reverse else tuple(tensors), "test-binding")


class YolopPostprocessConfigTests(unittest.TestCase):
    def test_config_preserves_binary64_and_has_canonical_identity(self):
        threshold = math.nextafter(0.5, 0)
        config = YolopPostprocessConfig(iou_threshold=threshold)
        self.assertEqual(config.iou_threshold.hex(), threshold.hex())
        raw = json.dumps(config.to_dict(), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("ascii")
        self.assertEqual(config.sha256, hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(config.sha256, YolopPostprocessConfig(iou_threshold=0.5).sha256)

    def test_invalid_config_and_types_fail(self):
        for kwargs in ({"confidence": True}, {"confidence": float("nan")}, {"confidence": -0.1},
                       {"iou_threshold": float("inf")}, {"iou_threshold": 1.01},
                       {"max_det": 0}, {"max_det": 501}, {"max_det": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                YolopPostprocessConfig(**kwargs)
        with self.assertRaises(TypeError):
            decode_yolop(object())

    def test_module_import_is_lazy_without_numpy_cv2_or_sdk(self):
        script = ("import sys;sys.path.insert(0,sys.argv[1]);"
                  "import xenova_vehicle.perception.yolop;"
                  "assert not any(m in sys.modules for m in ('numpy','cv2','depthai'))")
        result = subprocess.run([sys.executable, "-B", "-c", script, str(_JETSON / "src")],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(importlib.util.find_spec("numpy"), "approved NumPy is not installed")
class YolopPostprocessTests(unittest.TestCase):
    def test_float32_product_and_strict_binary64_thresholds(self):
        result = decode_yolop(packet([[8, 80, 4, 4, 0.5, 1], [18, 80, 4, 4, 1, 0.5],
                                     [28, 80, 4, 4, 0.50048828125, 0.99951171875]]))
        self.assertEqual([box.row for box in result.detections_nn], [2])
        self.assertEqual(result.detections_nn[0].score, 0.5002439022064209)
        self.assertEqual(result.counts["objectness_pass"], 2)
        exact = packet([[8, 80, 4, 4, 1, 1]])
        self.assertFalse(decode_yolop(exact, YolopPostprocessConfig(confidence=1)).detections_nn)
        self.assertEqual(len(decode_yolop(exact, YolopPostprocessConfig(
            confidence=math.nextafter(1.0, 0))).detections_nn), 1)

    def test_nms_uses_product_order_and_original_row_ties(self):
        rows = [[8, 80, 4, 4, .875, .625], [8, 80, 4, 4, .75, .875],
                [20, 80, 4, 4, 1, 1], [20, 80, 4, 4, 1, 1]]
        result = decode_yolop(packet(rows))
        self.assertEqual([box.row for box in result.detections_nn], [2, 1])
        self.assertEqual(result.counts["nms_suppressed"], 2)

    def test_fp32_iou_equal_and_nextafter_binary64_threshold(self):
        source = packet([[1.5, 80.5, 3, 1, 1, 1], [2.5, 80.5, 3, 1, .75, 1]])
        self.assertEqual([d.row for d in decode_yolop(source, YolopPostprocessConfig(iou_threshold=.5)).detections_nn], [0, 1])
        self.assertEqual([d.row for d in decode_yolop(source, YolopPostprocessConfig(
            iou_threshold=math.nextafter(.5, 0))).detections_nn], [0])

    def test_no_sigmoid_anchor_or_coordinate_normalization_and_source_clip(self):
        result = decode_yolop(packet([[0, 70, 4, 4, 1, 1], [319.5, 249.5, 3, 3, .75, 1]]))
        self.assertEqual(result.detections_nn[0].xyxy, (-2, 68, 2, 72))
        self.assertEqual(result.detections_source[0].xyxy, (0, 0, 4, 4))
        self.assertEqual(result.detections_source[1].xyxy, (636, 356, 640, 360))

    def test_nms_precedes_roi_and_fully_padding_has_no_source_candidate(self):
        result = decode_yolop(packet([[50, 60, 100, 20, 1, 1], [50, 70, 100, 20, .75, 1]]))
        self.assertEqual([box.row for box in result.detections_nn], [0])
        self.assertEqual(result.detections_source, ())
        self.assertEqual(result.counts["source_unobserved_after_nms"], 1)

    def test_nonpositive_and_fp32_collapsed_boxes_drop_but_tiny_positive_survives(self):
        smallest = 2**-24
        rows = [[10, 80, 0, 2, 1, 1], [20, 80, 2, -1, 1, 1],
                [320, 100, smallest, 2, 1, 1], [0, 70, smallest, 2, 1, 1], [8, 80, 1, 1, 1, 1]]
        result = decode_yolop(packet(rows))
        self.assertEqual([box.row for box in result.detections_nn], [3, 4])
        self.assertEqual(result.counts["invalid_nonpositive_xywh"], 2)
        self.assertEqual(result.counts["invalid_fp32_xyxy"], 1)
        self.assertEqual(result.detections_source[0].xyxy, (0, 0, smallest, 2))

    def test_all6300_before_nms_and_bounded500_kept(self):
        rows = [[(index % 90) * 3 + 1, (index // 90) * 3 + 1, 1, 1, 1, 1] for index in range(6300)]
        result = decode_yolop(packet(rows))
        self.assertEqual([box.row for box in result.detections_nn], list(range(500)))
        self.assertEqual(result.counts["nms_candidates"], 6300)
        self.assertEqual(result.counts["nms_unclassified_at_cap"], 5800)
        self.assertEqual(result.counts["nms_suppressed"], 0)
        self.assertFalse(result.counts["nms_input_pre_topk"])
        self.assertLessEqual(result.counts["nms_pairwise_tests"], 500 * 6300)
        limited = decode_yolop(packet(rows), YolopPostprocessConfig(max_det=2))
        self.assertEqual([box.row for box in limited.detections_nn], [0, 1])

    def test_pre_nms_top500_would_lose_later_distinct_candidate(self):
        rows = [[8, 80, 4, 4, 1, 1]] * 500 + [[100, 80, 4, 4, .75, 1]]
        result = decode_yolop(packet(rows))
        self.assertEqual([box.row for box in result.detections_nn], [0, 500])
        self.assertEqual(result.counts["nms_candidates"], 501)
        self.assertEqual(result.counts["nms_suppressed"], 499)

    def test_score_domain_rejects_every_head_without_clamp_even_low_obj_row(self):
        for column, value in ((4, -2**-24), (4, 1.0009765625), (5, -2**-24), (5, 1.0009765625)):
            row = [8, 80, 4, 4, 0, 0]
            row[column] = value
            with self.subTest(column=column, value=value), self.assertRaisesRegex(ValueError, "score-domain"):
                decode_yolop(packet([row]))
        for name in ("road", "lane"):
            for background, foreground in ((-2**-24, 0), (1.0009765625, 0),
                                           (0, -2**-24), (0, 1.0009765625)):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "score-domain"):
                    decode_yolop(packet(**{name: [(102399, background, foreground)]}))
        result = decode_yolop(packet([[8, 80, 1, 1, 2**-24, 2**-24]],
                                     road=[(0, -0.0, 0), (1, 0, 2**-24), (2, 1, 1)],
                                     lane=[(0, 0, 1)]), YolopPostprocessConfig(confidence=0))
        self.assertEqual(len(result.detections_nn), 1)
        self.assertEqual(result.road.argmax_nn.ravel()[:3].tolist(), [0, 1, 0])
        source = packet()
        data = bytearray(source.payload)
        struct.pack_into("<e", data, 6299 * 12 + 5 * 2, 1.0009765625)
        with self.assertRaisesRegex(ValueError, "score-domain"):
            decode_yolop(replace(source, payload=bytes(data)))

    def test_mask_channel_tie_roi_boundaries_and_exact2x_source_nearest(self):
        indices = [0, 69 * 320, 70 * 320, 249 * 320 + 319, 250 * 320, 102399]
        result = decode_yolop(packet(road=[(index, 0, 1) for index in indices],
                                     lane=[(70 * 320, .5, .5), (70 * 320 + 1, .5, .50048828125)]))
        import numpy as np
        self.assertEqual(int(result.road.argmax_nn.sum()), 6)
        self.assertEqual(int(result.road.content.sum()), 2)
        self.assertEqual(int(result.road.source.sum()), 8)
        self.assertEqual(int(result.lane.content.sum()), 1)
        self.assertFalse(result.road.observed_nn[:70].any() or result.road.observed_nn[250:].any())
        self.assertTrue(result.road.observed_nn[70:250].all())
        self.assertEqual(result.road.observed_nn.dtype, np.dtype(bool))
        self.assertEqual(result.road.content.shape, (180, 320))
        self.assertEqual(result.road.source.shape, (360, 640))
        for y in range(360):
            self.assertTrue(np.array_equal(result.road.source[y], result.road.argmax_nn[70 + y // 2].repeat(2)))

    def test_runtime_reverse_flat_and_nan_padding_do_not_change_semantics(self):
        rows = [[8, 80, 4, 4, 1, 1]]
        base = decode_yolop(packet(rows, road=[(70 * 320, 0, 1)]))
        for source in (packet(rows, road=[(70 * 320, 0, 1)], prefix=64, suffix=128, reverse=True),
                       packet(rows, road=[(70 * 320, 0, 1)], flat=True)):
            result = decode_yolop(source)
            self.assertEqual(result.detections_nn, base.detections_nn)
            self.assertEqual(result.road.argmax_nn.tobytes(), base.road.argmax_nn.tobytes())
            self.assertEqual(result.layers["det_out"].shape, (1, 6300, 6))
            self.assertEqual(result.layers["det_out"].dtype.str, "<f2")

    def test_missing_nan_or_malformed_tensor_fails_at_packet_boundary(self):
        source = packet()
        for changes in ({"tensors": source.tensors[:2]},
                        {"tensors": (source.tensors[0], source.tensors[0], source.tensors[2])},
                        {"tensors": (replace(source.tensors[0], dims=(6, 6300, 1)), *source.tensors[1:])}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(source, **changes)
        data = bytearray(source.payload)
        struct.pack_into("<H", data, 0, 0x7E00)
        with self.assertRaisesRegex(ValueError, "non-finite"):
            replace(source, payload=bytes(data))

    def test_packet_strong_reference_and_bytes_backed_readonly_array_lifetimes(self):
        source = packet([[8, 80, 4, 4, 1, 1]], road=[(70 * 320, 0, 1)])
        reference = weakref.ref(source)
        result = decode_yolop(source)
        self.assertIs(result.packet, source)
        original = result.road.source.tobytes()
        del source
        gc.collect()
        self.assertIsNotNone(reference())
        decode_yolop(packet(road=[(71 * 320, 0, 1)]))
        self.assertEqual(result.road.source.tobytes(), original)
        for array in (*result.layers.values(), result.road.argmax_nn, result.road.observed_nn,
                      result.road.content, result.road.source, result.lane.source):
            self.assertTrue(array.flags.c_contiguous)
            self.assertFalse(array.flags.writeable)
            with self.assertRaises(ValueError):
                array.flags.writeable = True
        self.assertTrue(all(type(value) is bytes for value in result.road.owner_bytes))
        self.assertEqual(result.packet.context_key, "test-binding")
        self.assertEqual((result.parser_id, result.preprocessing_id), (PARSER_ID, PREPROCESSING_ID))
        self.assertEqual(result.reference_utils_sha256, REFERENCE_UTILS_SHA256)
        self.assertEqual(result.config_sha256, result.config.sha256)
        self.assertFalse(result.live_authority or result.nn_ready or result.metric_ready)

    def test_independent_scalar20_vectors_when_prepared_evidence_is_present(self):
        path = _AUDIT / "s04-scalar-oracle-vectors.json"
        if not path.is_file():
            self.skipTest("local independent scalar20 evidence is not present")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "d3de5bc4c3170da0d53602276ad359b23cb3e73be0bac8b68267e3dfdbdb8efd")
        evidence = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual((evidence["case_count"], evidence["reference_sha256"]), (20, REFERENCE_UTILS_SHA256))
        for case in evidence["cases"]:
            with self.subTest(case=case["name"]):
                if case["kind"] == "detection":
                    settings = case["settings"]
                    config = YolopPostprocessConfig(settings.get("confidence", .5), settings.get("overlap", .3),
                                                   settings.get("max_det", 500))
                    actual = decode_yolop(packet(case["rows"]), config)
                    self.assertEqual([item.descriptor() for item in actual.detections_nn], case["expected"])
                elif case["kind"] == "continuous_edge_geometry":
                    left, top, right, bottom = case["nn_xyxy"]
                    actual = decode_yolop(packet([[(left + right) / 2, (top + bottom) / 2,
                                                   right - left, bottom - top, 1, 1]]))
                    value = list(actual.detections_source[0].xyxy) if actual.detections_source else None
                    self.assertEqual(value, case["expected_source_xyxy"])
                elif "background" in case:
                    updates = [(index, a, b) for index, (a, b) in enumerate(zip(case["background"], case["foreground"]))]
                    actual = decode_yolop(packet(road=updates))
                    self.assertEqual(actual.road.argmax_nn.ravel()[:len(updates)].tolist(), case["expected_flat_mask"])
                else:
                    actual = decode_yolop(packet(road=[(index, 0, 1) for index in case["foreground_indices"]]))
                    self.assertEqual(hashlib.sha256(actual.road.argmax_nn.tobytes()).hexdigest(), case["expected_mask_sha256"])
                    valid = (actual.road.argmax_nn & actual.road.observed_nn).ravel().nonzero()[0].tolist()
                    self.assertEqual(valid, case["valid_foreground_indices"])

    def test_real9_outputs_against_independent_scalar_hashes_when_present(self):
        path = _AUDIT / "s04-real-evidence-scalar-expectations.json"
        if not path.is_file():
            self.skipTest("local real9 independent expectations are not present")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "59a0b3cacd7831352be30be2b80e89b205e7c239aa47b161d799387ef6bcc42e")
        from xenova_vehicle.recording.yolop_reader import YolopDiagnosticReader
        evidence = json.loads(path.read_text(encoding="utf-8"))
        readers = {}
        for case in evidence["cases"]:
            key = case["dataset"]
            dataset = _JETSON / "build/yolop-bench-20261107" / key
            if not dataset.is_dir():
                self.skipTest("local real9 dataset payloads are not present")
            if key not in readers:
                readers[key] = {f"{bundle.ordinal:04d}": bundle for bundle in YolopDiagnosticReader(dataset).samples()}
            bundle = readers[key][case["sample_ordinal"]]
            self.assertEqual(bundle.metadata["rgb_source_key"], case["rgb_source_key"])
            self.assertEqual(str(bundle.nn_output.sdk_us), case["nn_sdk_timestamp_us"])
            sample_path = dataset / "samples" / case["sample_ordinal"]
            self.assertEqual(hashlib.sha256((sample_path / "source.json").read_bytes()).hexdigest(),
                             case["source_json_sha256"])
            self.assertEqual(hashlib.sha256((sample_path / "sample-index.json").read_bytes()).hexdigest(),
                             case["sample_index_sha256"])
            for name, expected in case["payloads"].items():
                self.assertEqual(hashlib.sha256(bundle.payloads[name]).hexdigest(), expected["sha256"])
            actual = decode_yolop(bundle.nn_output)
            self.assertEqual([item.descriptor() for item in actual.detections_nn], case["detection_candidates"])
            for name, value in (("drive_area_seg", actual.road), ("lane_line_seg", actual.lane)):
                expected = case["masks"][name]
                self.assertEqual(int(value.argmax_nn.sum()), expected["nn_foreground_count"])
                self.assertEqual(int(value.content.sum()), expected["content_foreground_count"])
                self.assertEqual(hashlib.sha256(value.argmax_nn.tobytes()).hexdigest(), expected["nn_mask_sha256"])
                self.assertEqual(hashlib.sha256(value.content.tobytes()).hexdigest(), expected["content_mask_sha256"])

    def test_six_independent_nearest_mask_hashes_when_present(self):
        path = _AUDIT / "s04-nearest-mask-goldens.json"
        if not path.is_file():
            self.skipTest("local independent nearest-mask expectations are not present")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "cfdb4becb2be7b612ee321507daf182c622efa74b1667b6612c97f186bbbf43e")
        evidence = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["case_count"], 6)
        for case in evidence["cases"]:
            with self.subTest(case=case["name"]):
                labels = bytearray(102400)
                if case["name"] == "all_foreground":
                    labels[:] = b"\x01" * len(labels)
                elif case["name"] == "content_corner_impulses":
                    labels[70 * 320] = labels[249 * 320 + 319] = 1
                elif case["name"] == "two_center_columns":
                    for y in range(70, 250):
                        labels[y * 320 + 159] = labels[y * 320 + 160] = 1
                elif case["name"] == "content_checkerboard":
                    for y in range(70, 250):
                        for x in range(320):
                            labels[y * 320 + x] = ((y - 70) + x) % 2
                elif case["name"] == "padding_only":
                    labels[:70 * 320] = b"\x01" * (70 * 320)
                    labels[250 * 320:] = b"\x01" * (70 * 320)
                else:
                    self.assertEqual(case["name"], "all_background")
                foreground = bytearray(204800)
                foreground[1::2] = labels.translate(bytes(0x3C if value == 1 else 0 for value in range(256)))
                actual = decode_yolop(packet(road_foreground=bytes(foreground)))
                self.assertEqual(hashlib.sha256(actual.road.argmax_nn.tobytes()).hexdigest(), case["nn_mask_sha256"])
                self.assertEqual(hashlib.sha256(actual.road.source.tobytes()).hexdigest(), case["source_mask_sha256"])
                self.assertEqual(int(actual.road.source.sum()), case["source_foreground_pixels"])


if __name__ == "__main__":
    unittest.main()
