"""YOLOP 순수 계약의 양성·반례. fixture는 합성이고 장치를 열지 않는다."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from fractions import Fraction
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.oak.observations import ClockEvidence
from xenova_vehicle.oak.yolop_association import YolopAssociation
from xenova_vehicle.oak.yolop_frames import NNInputStamp, NNPacket
from xenova_vehicle.recording.reader import DatasetError
from xenova_vehicle.recording.yolop_contract import (
    PAYLOAD_NAMES, decode_sample, validate_manifest, validate_receipt,
)
from yolop_dataset_fixtures import HARDWARE_VERIFIED, canonical, make_dataset, read_json


class YolopContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = make_dataset(Path(self.temporary.name) / "dataset", samples=1)
        self.raw_manifest = read_json(self.path / "bench-manifest.json")
        self.manifest = validate_manifest(self.raw_manifest)
        directory = self.path / "samples/0001"
        self.metadata = read_json(directory / "source.json")
        self.payloads = {name: (directory / name).read_bytes() for name in PAYLOAD_NAMES}
        self.receipt = read_json(self.path / "yolop-bench-result.json")
        self.total = sum(item.stat().st_size for item in self.path.rglob("*")
                         if item.is_file() and item.name != "yolop-bench-result.json")

    def decode(self, metadata=None, payloads=None, ordinal=1):
        return decode_sample(self.metadata if metadata is None else metadata,
                             self.payloads if payloads is None else payloads,
                             manifest=self.manifest, ordinal=ordinal)

    def receipt_summary(self, receipt=None, **changes):
        values = dict(sample_count=1, failure_count=0, total_written_bytes=self.total, tail_count=0)
        values.update(changes)
        return validate_receipt(self.manifest, self.receipt if receipt is None else receipt, **values)

    def test_synthetic_typed_bundle_keeps_original_metadata_and_owned_bytes(self):
        self.assertFalse(HARDWARE_VERIFIED)
        original = deepcopy(self.metadata)
        bundle = self.decode()
        self.assertEqual(bundle.metadata, original)
        self.assertEqual(bundle.metadata["nn_output"]["validation_elapsed_us"], 37)
        self.assertEqual(bundle.nn_output.validation_elements, 447400)
        self.assertEqual(bundle.nn_output.layer_bytes("det_out"), self.payloads["det_out.fp16"])
        self.assertEqual(bundle.availability_j_us, max(item.received_j_us for item in (
            bundle.rgb, bundle.depth, bundle.nn_input, bundle.nn_output)))
        self.assertEqual(bundle.input_kind, "REPLAY")
        self.assertFalse(bundle.live_authority or bundle.nn_ready or bundle.metric_ready)
        self.assertEqual(bundle.depth_time_qualification,
                         "recorded_RGB_bridge_compatibility_only; original_depth_bridge_missing")
        self.metadata["rgb"]["sequence"] = "999"
        self.payloads["rgb.nv12"] = b"changed"
        self.assertEqual(bundle.metadata, original)
        self.assertEqual(len(bundle.payloads["rgb.nv12"]), 345600)
        with self.assertRaises(FrozenInstanceError):
            bundle.live_authority = True

    def test_all_contract_functions_do_not_open_model_or_other_paths(self):
        with patch("builtins.open", side_effect=AssertionError("no file access")), \
                patch.object(Path, "open", side_effect=AssertionError("no Path access")):
            parsed = validate_manifest(self.raw_manifest)
            decode_sample(self.metadata, self.payloads, manifest=parsed, ordinal=1)
            result = validate_receipt(parsed, self.receipt, sample_count=1, failure_count=0,
                                      total_written_bytes=self.total, tail_count=0)
        self.assertTrue(result["source_complete"])
        self.assertEqual(parsed["profile"]["asset_dir"], "/nonexistent/synthetic-only/yolop-assets")

    def test_profile_hash_uses_original_opaque_linux_strings(self):
        modified = deepcopy(self.raw_manifest)
        modified["profile"]["asset_dir"] = "C:\\different"
        with self.assertRaisesRegex(DatasetError, "digest"):
            validate_manifest(modified)

    def test_manifest_provenance_preprocessing_and_identity_mismatch_rejected(self):
        changes = (
            lambda m: m.update(nn_ready=True),
            lambda m: m.update(schema_version=True),
            lambda m: m.update(nn_context_key="0" * 64),
            lambda m: m["context"]["stream_instances"].update(nn_input=m["context"]["stream_instances"]["rgb_video"]),
            lambda m: m["setup"]["model_asset"].update(blob_bytes=1),
            lambda m: m["setup"]["model_asset"]["files"][0].update(sha256="0" * 64),
            lambda m: m["setup"]["model_asset"].update(device_readback_verified=True),
            lambda m: m["setup"]["preprocessing"].update(padding_is_observation=True),
            lambda m: m["setup"].update(nn_context_key="f" * 64),
            lambda m: m["setup"]["sdk_blob_metadata"].update(num_shaves=5),
            lambda m: m["setup"]["graph_declaration"]["declared_settings"]["rgb"].update(size_wh=[320, 320]),
            lambda m: m["first_nn_layout_probe"]["golden"].update(matched=False),
            lambda m: m["first_nn_layout_probe"]["golden"].update(elements=1),
            lambda m: m["first_nn_layout_probe"]["tensors"][0].update(strides=[]),
        )
        for mutate in changes:
            with self.subTest(mutate=mutate):
                modified = deepcopy(self.raw_manifest)
                mutate(modified)
                with self.assertRaises(DatasetError):
                    validate_manifest(modified)

    def test_false_numeric_types_in_recorded_descriptors_rejected(self):
        for section, key, value in (("rgb", "payload_length", True), ("depth", "invalid_depth_value", False),
                                    ("nn_output", "validation_elements", True), ("nn_input", "metadata_only", 1)):
            modified = deepcopy(self.metadata)
            modified[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(DatasetError):
                self.decode(modified)

    def test_nn_metadata_only_null_selected_golden_and_original_cost_preserved(self):
        self.assertIsNone(self.metadata["nn_output"]["golden"])
        bundle = self.decode()
        self.assertIsNone(bundle.nn_output.golden)
        self.assertEqual(bundle.metadata["nn_output"]["validation_elapsed_us"], 37)
        self.assertEqual(bundle.metadata["nn_input"]["payload_length"], 0)
        modified = deepcopy(self.metadata)
        modified["nn_input"]["payload_length"] = 307200
        with self.assertRaises(DatasetError):
            self.decode(modified)

    def test_all_raw_descriptor_units_strides_lengths_are_checked(self):
        for section, key, value in (("depth", "depth_unit", "meter"), ("depth", "row_stride_bytes", 640),
                                    ("depth", "exposure_us", -1), ("rgb", "dtype", "float32"),
                                    ("rgb", "width", 320), ("rgb", "exposure_us", True)):
            modified = deepcopy(self.metadata)
            modified[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(DatasetError):
                self.decode(modified)

    def test_runtime_layout_overlap_stride_truncation_and_layer_names_rejected(self):
        changes = ({"offset": 1}, {"offset": 75648}, {"strides": []}, {"dims": [6, 6300, 1]},
                   {"byte_length": 1}, {"order_code": 17185}, {"data_type_code": 3}, {"name": "other"})
        for values in changes:
            modified = deepcopy(self.metadata)
            modified["nn_output"]["tensors"][0].update(values)
            with self.subTest(values=values), self.assertRaises(DatasetError):
                self.decode(modified)
        data = dict(self.payloads)
        data["nn-buffer.bin"] = data["nn-buffer.bin"][:-1]
        with self.assertRaises(DatasetError):
            self.decode(payloads=data)

    def test_active_nonfinite_rejected_even_if_derived_slice_is_updated(self):
        for tensor in self.metadata["nn_output"]["tensors"]:
            for bits in (0x7C00, 0xFC00, 0x7E00):
                data = dict(self.payloads)
                raw = bytearray(data["nn-buffer.bin"])
                struct.pack_into("<H", raw, tensor["offset"], bits)
                data["nn-buffer.bin"] = bytes(raw)
                data[tensor["name"] + ".fp16"] = bytes(raw[tensor["offset"]:tensor["offset"] + tensor["byte_length"]])
                with self.subTest(layer=tensor["name"], bits=bits), self.assertRaisesRegex(DatasetError, "non-finite"):
                    self.decode(payloads=data)

    def test_compiler_gap_is_not_active_fp16_and_derived_bytes_must_match(self):
        data = dict(self.payloads)
        raw = bytearray(data["nn-buffer.bin"])
        raw[75600:75648] = b"\xff" * 48
        data["nn-buffer.bin"] = bytes(raw)
        self.decode(payloads=data)
        data["det_out.fp16"] = b"\x00\x3c" + data["det_out.fp16"][2:]
        with self.assertRaisesRegex(DatasetError, "derived"):
            self.decode(payloads=data)

    def test_context_source_keys_independent_depth_sequence_and_deltas(self):
        self.assertNotEqual(self.metadata["rgb"]["sequence"], self.metadata["depth"]["sequence"])
        changes = (
            lambda m: m["context"].update(pipeline_instance=m["context"]["capture_epoch"]),
            lambda m: m["nn_input"].update(sequence="11"),
            lambda m: m["nn_output"].update(device_timestamp_us="1"),
            lambda m: m["nn_output"].update(sdk_timestamp_us="1"),
            lambda m: m["rgb_source_key"].__setitem__(0, m["context"]["stream_instances"]["depth_right"]),
            lambda m: m.update(source_timestamp_delta_us=1),
            lambda m: m.update(arrival_difference_us=1),
            lambda m: m.update(depth_candidate_sdk_delta_us=0),
        )
        for mutate in changes:
            modified = deepcopy(self.metadata)
            mutate(modified)
            with self.subTest(mutate=mutate), self.assertRaises(DatasetError):
                self.decode(modified)

    def test_availability_uses_all_four_receives_and_negative_arrival_is_valid(self):
        modified = deepcopy(self.metadata)
        source = int(modified["rgb"]["sdk_timestamp_us"])
        modified["rgb"]["received_j_us"] = str(source + 350000)
        modified["arrival_difference_us"] = -49000
        result = self.decode(modified)
        self.assertEqual(result.availability_j_us, source + 350000)
        self.assertEqual(result.metadata["arrival_difference_us"], -49000)

    def test_original_rgb_age_ring_and_depth_tolerance_are_not_refreshed(self):
        changes = (
            lambda m: m["nn_output"].update(received_j_us=str(int(m["rgb"]["received_j_us"]) + 500001)),
            lambda m: m["depth"].update(sdk_timestamp_us=str(int(m["rgb"]["sdk_timestamp_us"]) + 10001)),
            lambda m: m["capture_clock"]["bridge_sample"].update(j_after_us="1"),
            lambda m: m["capture_clock"]["sdk_host_timestamp_j_bracket_us"].__setitem__(0, "1"),
            lambda m: m["capture_clock"].update(metric_time_qualified=True),
            lambda m: m["capture_clock"].update(oak_sync_error_bound_us=10),
        )
        for mutate in changes:
            modified = deepcopy(self.metadata)
            mutate(modified)
            with self.subTest(mutate=mutate), self.assertRaises(DatasetError):
                self.decode(modified)

    def test_depth_receive_wholly_before_or_after_compatible_age_is_rejected(self):
        rgb_low, rgb_high = map(int, self.metadata["capture_clock"]["sdk_host_timestamp_j_bracket_us"])
        delta = self.metadata["depth_candidate_sdk_delta_us"]
        depth_low, depth_high = rgb_low + delta, rgb_high + delta
        for receive in (depth_low - 1_000_000, depth_low + 577706):
            modified = deepcopy(self.metadata)
            modified["depth"]["received_j_us"] = str(receive)
            with self.subTest(receive=receive), self.assertRaisesRegex(DatasetError, "depth receive time"):
                self.decode(modified)

    def test_depth_age_interval_boundary_overlap_only_establishes_compatibility(self):
        rgb_low, rgb_high = map(int, self.metadata["capture_clock"]["sdk_host_timestamp_j_bracket_us"])
        delta = self.metadata["depth_candidate_sdk_delta_us"]
        depth_low, depth_high = rgb_low + delta, rgb_high + delta
        for receive, accepted in ((depth_low - 1, False), (depth_low, True),
                                  (depth_high + 500000, True), (depth_high + 500001, False)):
            modified = deepcopy(self.metadata)
            modified["depth"]["received_j_us"] = str(receive)
            with self.subTest(receive=receive):
                if accepted:
                    result = self.decode(modified)
                    self.assertEqual(result.metadata["depth"]["received_j_us"], str(receive))
                    self.assertEqual(result.depth_time_qualification,
                                     "recorded_RGB_bridge_compatibility_only; original_depth_bridge_missing")
                    self.assertFalse(result.metric_ready)
                else:
                    with self.assertRaisesRegex(DatasetError, "depth receive time"):
                        self.decode(modified)

    def test_depth_sdk_strictly_after_bridge_cannot_precede_bridge_call(self):
        sample = self.metadata["capture_clock"]["bridge_sample"]
        for relative_sdk, accepted in ((-1, True), (0, True), (1, False)):
            modified = deepcopy(self.metadata)
            sdk = int(sample["sdk_us"]) + relative_sdk
            modified["depth"]["sdk_timestamp_us"] = str(sdk)
            modified["depth"]["received_j_us"] = str(int(sample["j_before_us"]) - 1)
            modified["depth_candidate_sdk_delta_us"] = sdk - int(modified["rgb"]["sdk_timestamp_us"])
            with self.subTest(relative_sdk=relative_sdk):
                if accepted:
                    self.decode(modified)
                else:
                    with self.assertRaisesRegex(DatasetError, "SDK/J causal order"):
                        self.decode(modified)

    def test_fraction_clock_equal_truncated_sdk_keeps_valid_prior_depth_reuse(self):
        # SDK(t)=t, J(t)=t+0.5us를 각각 floor한다. 실제 D 수신은 B 전에
        # 끝났지만 D와 B의 SDK 정수값은 같다. 실물 시계 오차 측정은 아니다.
        base = 2_100_000

        def sdk(relative):
            return (Fraction(base) + relative).__floor__()

        def j(relative):
            return (Fraction(base) + relative + Fraction(1, 2)).__floor__()

        bridge_a = {"j_before_us": j(Fraction(-19, 10)), "sdk_us": sdk(Fraction(-18, 10)),
                    "j_after_us": j(Fraction(-17, 10))}
        bridge_b = {"j_before_us": j(Fraction(8, 10)), "sdk_us": sdk(Fraction(9, 10)),
                    "j_after_us": j(Fraction(1))}
        key = self.manifest["context_key"]
        rgb = RawFrame("rgb_video", 10, 2_000_001, sdk(Fraction(1)), j(Fraction(21, 10)),
                       640, 360, "NV12", self.payloads["rgb.nv12"], 30006)
        depth = RawFrame("depth_right", 4, 2_000_000, sdk(Fraction(0)), j(Fraction(1, 10)),
                         640, 480, "RAW16_LE", self.payloads["depth.raw16le"], 30005)
        stamp = NNInputStamp("nn_input", rgb.sequence, rgb.device_us, rgb.sdk_us,
                             j(Fraction(199, 2)), 320, 320, "BGR888p", key)
        packet = NNPacket("nn_output", rgb.sequence, rgb.device_us, rgb.sdk_us,
                          j(Fraction(201, 2)), self.payloads["nn-buffer.bin"], self.decode().nn_output.tensors, key)
        bridge = ClockEvidence()
        bridge.update(bridge_a)
        depth_clock = bridge.describe(depth)
        depth_age = depth.received_j_us - int(depth_clock["sdk_host_timestamp_j_bracket_us"][0])
        bridge.update(bridge_b)
        clocks = {item.stream: bridge.describe(item) for item in (rgb, stamp, packet)}
        ages = {"depth_right": depth_age, **{item.stream: item.received_j_us
                - int(clocks[item.stream]["sdk_host_timestamp_j_bracket_us"][0])
                for item in (rgb, stamp, packet)}}
        self.assertEqual(ages, {"depth_right": 1, "rgb_video": 1, "nn_input": 99, "nn_output": 100})
        association = YolopAssociation(key)
        matches = [match for item in (depth, rgb, stamp, packet)
                   for match in association.observe(item, context_key=key)]
        self.assertEqual(len(matches), 1)
        modified = deepcopy(self.metadata)
        modified.update(rgb=rgb.descriptor(), depth=depth.descriptor(), nn_input=stamp.descriptor(),
                        nn_output=packet.descriptor(), capture_clock=clocks["rgb_video"],
                        rgb_source_key=[self.manifest["context"]["stream_instances"]["rgb_video"],
                                        str(rgb.sequence), str(rgb.device_us)], source_timestamp_delta_us=0,
                        arrival_difference_us=packet.received_j_us - rgb.received_j_us,
                        depth_candidate_sdk_delta_us=depth.sdk_us - rgb.sdk_us)
        low, high = map(int, modified["capture_clock"]["sdk_host_timestamp_j_bracket_us"])
        delta = depth.sdk_us - rgb.sdk_us
        self.assertEqual([depth.received_j_us - (high + delta), depth.received_j_us - (low + delta)], [-2, 0])
        self.assertEqual(depth.sdk_us, bridge_b["sdk_us"])
        self.assertLess(depth.received_j_us, bridge_b["j_before_us"])
        result = self.decode(modified)
        self.assertEqual(result.depth, depth)
        self.assertEqual(result.metadata, modified)
        self.assertFalse(result.metric_ready)

    def test_identical_depth_can_be_reused_with_next_rgb_bridge_projection(self):
        first = self.decode()
        modified = deepcopy(self.metadata)
        for name in ("rgb", "nn_input", "nn_output"):
            modified[name]["sequence"] = str(int(modified[name]["sequence"]) + 1)
            for stamp in ("device_timestamp_us", "sdk_timestamp_us", "received_j_us"):
                modified[name][stamp] = str(int(modified[name][stamp]) + 1000)
        modified["rgb_source_key"][1:] = [modified["rgb"]["sequence"], modified["rgb"]["device_timestamp_us"]]
        modified["depth_candidate_sdk_delta_us"] -= 1000
        for key, stamp in modified["capture_clock"]["bridge_sample"].items():
            modified["capture_clock"]["bridge_sample"][key] = str(int(stamp) + 1000)
        modified["capture_clock"]["sdk_host_timestamp_j_bracket_us"] = [
            str(int(stamp) + 1000) for stamp in modified["capture_clock"]["sdk_host_timestamp_j_bracket_us"]]
        second = self.decode(modified)
        self.assertEqual(first.depth, second.depth)
        self.assertEqual(second.metadata["depth"], self.metadata["depth"])

    def test_payload_set_type_and_ordinal_caps(self):
        for data in ({k: v for k, v in self.payloads.items() if k != "det_out.fp16"},
                     {**self.payloads, "extra": b""}, {**self.payloads, "rgb.nv12": bytearray(345600)},
                     {**self.payloads, "nn-buffer.bin": bytes(1048577)}):
            with self.assertRaises(DatasetError):
                self.decode(payloads=data)
        for ordinal in (0, 4, True):
            with self.assertRaises(DatasetError):
                self.decode(ordinal=ordinal)

    def test_complete_receipt_does_not_require_all_nn_outputs_to_be_saved(self):
        self.assertGreater(self.receipt["last_worker_status"]["nn_bench"]["matched"], 1)
        result = self.receipt_summary()
        self.assertTrue(result["source_complete"])
        self.assertEqual(result["sample_count"], 1)
        self.assertEqual(result["writer_counter_qualification"], "final_exact")
        self.assertFalse(result["requires_allow_incomplete"] or result["continuous_nn_recording"])

    def test_complete_receipt_requires_actual_close_reap_and_final_writer(self):
        changes = (
            lambda r: r.update(complete=True, exit_code=130),
            lambda r: r.update(forced_termination=True),
            lambda r: r.update(process_exit_confirmed=False),
            lambda r: r.update(worker_exit_code=True),
            lambda r: r["last_worker_status"].update(kind="status"),
            lambda r: r["last_worker_status"].update(device_close_confirmed=False),
            lambda r: r["last_worker_status"]["recording"].update(writer_exit_confirmed=False),
            lambda r: r["last_worker_status"]["recording"].update(error="disk failure"),
            lambda r: r["last_worker_status"]["recording"].update(written_bytes=self.total - 1),
            lambda r: r["last_worker_status"]["recording"].update(saved_samples=0),
            lambda r: r["last_worker_status"]["nn_bench"].update(matched=0),
        )
        for mutate in changes:
            changed = deepcopy(self.receipt)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(DatasetError):
                self.receipt_summary(changed)

    def test_interrupt_receipt_preserves_complete_selected_prefix_but_is_incomplete(self):
        changed = deepcopy(self.receipt)
        changed.update(complete=False, exit_code=130, reason="interrupted")
        changed["last_worker_status"]["reason"] = "stopped"
        result = self.receipt_summary(changed)
        self.assertFalse(result["source_complete"])
        self.assertTrue(result["requires_allow_incomplete"])
        self.assertEqual(result["receipt_status"], "final")
        self.assertEqual(result["writer_counter_qualification"], "final_exact")

    def test_missing_and_initial_reservation_are_distinguished_from_corruption(self):
        result = validate_receipt(self.manifest, None, sample_count=1, failure_count=0,
                                  total_written_bytes=self.total, tail_count=0)
        self.assertEqual(result["receipt_status"], "missing")
        initial = deepcopy(self.receipt)
        initial.update(complete=False, exit_code=1, reason="starting")
        initial.pop("last_worker_status")
        initial["context"].pop("startup_deadline_j_us")
        self.assertEqual(self.receipt_summary(initial)["receipt_status"], "stale_or_reservation")

    def test_stale_status_lower_bound_is_allowed_but_impossible_counter_is_not(self):
        changed = deepcopy(self.receipt)
        changed.update(complete=False, exit_code=1, reason="worker_error")
        changed["last_worker_status"]["kind"] = "status"
        recording = changed["last_worker_status"]["recording"]
        recording.update(saved_samples=0, accepted_samples=1, pending_frames=1,
                         written_frames=0, written_bytes=100, writer_exit_confirmed=False)
        self.assertEqual(self.receipt_summary(changed)["writer_counter_qualification"], "stale_lower_bound")
        recording["saved_samples"] = 2
        with self.assertRaises(DatasetError):
            self.receipt_summary(changed)

    def test_writer_drop_does_not_claim_continuous_nn_loss_or_prevent_later_samples(self):
        changed = deepcopy(self.receipt)
        changed["last_worker_status"]["recording"]["dropped_samples"] = 5
        result = self.receipt_summary(changed)
        self.assertTrue(result["source_complete"])
        self.assertEqual(result["writer_dropped_samples"], 5)
        self.assertFalse(result["continuous_nn_recording"])

    def test_complete_normal_writer_submissions_cannot_exceed_matched_inputs(self):
        changed = deepcopy(self.receipt)
        changed["last_worker_status"]["recording"]["dropped_samples"] = 29
        with self.assertRaisesRegex(DatasetError, "submissions exceed"):
            self.receipt_summary(changed)
        # 불완전 경로의 drop에는 실패 진단 제출도 포함될 수 있다.
        changed.update(complete=False, exit_code=1, reason="worker_error")
        self.assertFalse(self.receipt_summary(changed)["source_complete"])

    def test_failed_partial_tail_byte_interval_excludes_complete_prefix_loss(self):
        changed = deepcopy(self.receipt)
        changed.update(complete=False, exit_code=1, reason="worker_error")
        recording = changed["last_worker_status"]["recording"]
        recording.update(accepted_samples=2, pending_frames=1, error="short diagnostic file write",
                         written_bytes=self.total + 5)
        result = self.receipt_summary(changed, total_written_bytes=self.total + 10, tail_count=1, tail_bytes=10)
        self.assertEqual(result["writer_counter_qualification"], "failed_unindexed_tail_byte_interval")
        recording["written_bytes"] = self.total - 1
        with self.assertRaises(DatasetError):
            self.receipt_summary(changed, total_written_bytes=self.total + 10, tail_count=1, tail_bytes=10)

    def test_receipt_denominator_failure_only_and_tail_caps(self):
        changed = deepcopy(self.receipt)
        changed["last_worker_status"]["nn_bench"]["association"]["pending"]["rgb_video"] = 4
        with self.assertRaises(DatasetError):
            self.receipt_summary(changed)
        with self.assertRaises(DatasetError):
            self.receipt_summary(tail_count=2)
        with self.assertRaises(DatasetError):
            self.receipt_summary(total_written_bytes=64 * 1024**2)
        failure = validate_receipt(self.manifest, None, sample_count=0, failure_count=1,
                                  total_written_bytes=1000, tail_count=0)
        self.assertTrue(failure["no_nn_yield"])
        self.assertFalse(failure["source_complete"])
        self.assertEqual(failure["classification"], "failure_only")

    def test_public_source_counters_cannot_hide_impossible_rgb_or_nn_counts(self):
        changes = (
            lambda r: r["last_worker_status"]["observations"]["received_frames"].update(rgb_video=0),
            lambda r: r["last_worker_status"]["observations"]["received_frames"].update(rgb_video=True),
            lambda r: r["last_worker_status"]["observations"]["sequence_gaps"].update(depth_right=-1),
            lambda r: r["last_worker_status"]["observations"].update(candidate_pairs=True),
            lambda r: r["last_worker_status"]["observations"].update(bench_warmup_30_pairs=True),
            lambda r: r["last_worker_status"]["nn_bench"]["received"].update(nn_output=0),
            lambda r: r["last_worker_status"]["nn_bench"]["received"].update(nn_input=True),
            lambda r: r["last_worker_status"]["nn_bench"].update(validation_elements=True),
        )
        for mutate in changes:
            changed = deepcopy(self.receipt)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(DatasetError):
                self.receipt_summary(changed)


if __name__ == "__main__":
    unittest.main()
