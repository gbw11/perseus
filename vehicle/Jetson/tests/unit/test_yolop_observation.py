"""원 bundle 결박·공유 BGR·출처 사본·수명을 검사한다. fixture는 합성 자료다."""

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "replay"))
from yolop_dataset_fixtures import _manifest, _sample
from xenova_vehicle.recording.yolop_contract import decode_sample, validate_manifest
from xenova_vehicle.perception.observation import YolopDecoder
from xenova_vehicle.perception.yolop import YolopPostprocessConfig

try:
    import numpy as np
except ImportError:
    np = None
try:
    import cv2
    CV_READY = callable(getattr(cv2, "cvtColor", None))
except ImportError:
    CV_READY = False


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = _manifest(1)
        metadata, payloads = _sample(self.manifest, 1)
        self.sample = decode_sample(metadata, payloads, manifest=validate_manifest(self.manifest), ordinal=1)

    def test_import_and_constructor_remain_lazy(self):
        code = """import builtins
original=builtins.__import__
def guarded(name,*a,**kw):
 if name.split('.')[0] in ('numpy','cv2','depthai','torch','torchvision','vpi','paho','socket'):
  raise AssertionError(name)
 return original(name,*a,**kw)
builtins.__import__=guarded
from xenova_vehicle.perception.observation import YolopDecoder
from yolop_dataset_fixtures import _manifest
YolopDecoder(_manifest(1))
"""
        result = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True,
                                timeout=10, env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bad_contract_or_config_rejected(self):
        with self.assertRaises(ValueError):
            YolopDecoder(self.manifest, {})
        manifest = deepcopy(self.manifest)
        manifest["setup"]["preprocessing"]["scale_xy"][0] = 1
        with self.assertRaises(ValueError):
            YolopDecoder(manifest)

    def test_typed_and_metadata_mismatches_reject_before_color(self):
        decoder = YolopDecoder(self.manifest)
        cases = [None, replace(self.sample, availability_j_us=self.sample.availability_j_us + 1),
                 replace(self.sample, nn_output=None),
                 replace(self.sample, rgb=replace(self.sample.rgb, sequence=777)),
                 replace(self.sample, nn_output=replace(self.sample.nn_output, sequence=777))]
        changed = deepcopy(self.sample.metadata)
        changed["context"]["device_id"] = "FOREIGN"
        cases.append(replace(self.sample, metadata=changed))
        payloads = dict(self.sample.payloads)
        payloads["rgb.nv12"] = bytes([1]) + payloads["rgb.nv12"][1:]
        cases.append(replace(self.sample, payloads=payloads))
        with patch("xenova_vehicle.perception.observation.decode_rgb") as color:
            for sample in cases:
                with self.subTest(sample_type=type(sample).__name__):
                    with self.assertRaises(ValueError):
                        decoder.decode(sample)
            color.assert_not_called()

    @unittest.skipUnless(np is not None and CV_READY, "usable NumPy/OpenCV required")
    def test_shared_color_once_preserved_provenance_and_independent_lifetime(self):
        from xenova_vehicle.perception.decoders import decode_rgb
        manifest = deepcopy(self.manifest)
        decoder = YolopDecoder(manifest, YolopPostprocessConfig(confidence=.6))
        manifest["context"]["device_id"] = "caller changed copy"
        before = deepcopy(self.sample.metadata)
        with patch("xenova_vehicle.perception.observation.decode_rgb", wraps=decode_rgb) as color:
            first = decoder.decode(self.sample)
            color.assert_called_once_with(self.sample.rgb)
        first_bytes = first.rgb.bgr.tobytes()
        first.source_metadata["context"]["device_id"] = "result changed copy"
        first.provenance["model"]["asset_id"] = "result changed copy"
        second = decoder.decode(self.sample)
        self.assertEqual(self.sample.metadata, before)
        self.assertEqual(second.source_metadata, before)
        self.assertEqual(second.rgb.bgr.tobytes(), first_bytes)
        self.assertFalse(np.shares_memory(first.rgb.bgr, second.rgb.bgr))
        self.assertIs(first.sample, self.sample)
        self.assertIs(first.rgb.frame, self.sample.rgb)
        self.assertIs(first.depth.frame, self.sample.depth)
        self.assertIs(first.yolop.packet, self.sample.nn_output)
        self.assertEqual(second.provenance["model"]["asset_id"], self.manifest["setup"]["model_id"])
        self.assertEqual(second.provenance["config"]["confidence"], .6)
        self.assertEqual(second.availability_j_us, self.sample.availability_j_us)
        self.assertEqual(second.depth_time_qualification, self.sample.depth_time_qualification)
        self.assertEqual(second.input_kind, "REPLAY")
        self.assertFalse(second.live_authority or second.nn_ready or second.metric_ready)
        self.assertFalse(second.provenance["pixel_equivalence_verified"])
        self.assertTrue(all(type(v) is int and v >= 0 for v in second.costs_us.values()))


if __name__ == "__main__":
    unittest.main()
