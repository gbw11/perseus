"""S02 JSON 틀을 따른 zero-payload 합성 fixture. 실기 관측·성능 근거가 아니다."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.oak.yolop_frames import NNInputStamp, NNPacket, RuntimeTensor


HARDWARE_VERIFIED = False
ASSET_ID = "yolop-320x320-rvc2-ov2021.4-sh6-v1"
BLOB_SHA = "8bc407f90814c6092ce0d76f7ce8cee0aaec68236358113946fe0134a655c437"
_MODEL_FILES = [
    {"name": "Luxonis-Model-Zoo-LICENSE.txt", "bytes": 1084,
     "sha256": "227c65da2a2303032fa8e53c8cac203b9bf7e7e2abbb86ac974101b2093f3b84"},
    {"name": "YOLOP-LICENSE.txt", "bytes": 1082,
     "sha256": "97acb244135bb2ed8ca578990bc91b670d61d0c5c37fc67b17bdc252d865cd9d"},
    {"name": "yolop_320x320.bin", "bytes": 15872074,
     "sha256": "43d8c3539605a0cc2e4be5b355c9c2ca0c3544683e1a725cf1071390375d084a"},
    {"name": "yolop_320x320.xml", "bytes": 582488,
     "sha256": "96586619ad12e863a0747172aa485d1ca7b1d55f9030bb00a7efffb80bfd3e6d"},
    {"name": "yolop_320x320_openvino_2021.4_6shave.blob", "bytes": 15973184, "sha256": BLOB_SHA},
]
_TENSORS = (
    RuntimeTensor("det_out", 0, 801, 3, (1, 6300, 6), (75600, 12, 2), 0,
                  "DataType.FP16", "StorageOrder.CHW"),
    RuntimeTensor("drive_area_seg", 0, 17185, 4, (1, 2, 320, 320), (409600, 204800, 640, 2),
                  75648, "DataType.FP16", "StorageOrder.NCHW"),
    RuntimeTensor("lane_line_seg", 0, 17185, 4, (1, 2, 320, 320), (409600, 204800, 640, 2),
                  485248, "DataType.FP16", "StorageOrder.NCHW"),
)


def _compiler_metadata():
    def tensor(name, dtype, order, dims, offset=0):
        return {"name": name, "dataType": "DataType." + dtype, "order": "StorageOrder." + order,
                "dims": dims, "strides": [], "offset": offset}
    return {"sdk_inferred_version": "Version.VERSION_2021_4", "num_shaves": 6, "num_slices": 6,
            "stage_count": 295, "inputs": {"images": tensor("images", "U8F", "NCHW", [320, 320, 3, 1])},
            "outputs": {"det_out": tensor("det_out", "FP16", "CHW", [6, 6300, 1]),
                        "drive_area_seg": tensor("drive_area_seg", "FP16", "NCHW", [320, 320, 2, 1], 75648),
                        "lane_line_seg": tensor("lane_line_seg", "FP16", "NCHW", [320, 320, 2, 1], 485248)}}


def canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_bytes(canonical(value) + b"\n")


def refresh_index(sample_dir):
    """손상 반례에서 의도적으로 raw hash를 재계산할 때만 사용한다."""
    sample_dir = Path(sample_dir)
    payloads = {name: {"bytes": (sample_dir / name).stat().st_size,
                       "sha256": hashlib.sha256((sample_dir / name).read_bytes()).hexdigest()}
                for name in ("rgb.nv12", "depth.raw16le", "nn-buffer.bin", "det_out.fp16",
                             "drive_area_seg.fp16", "lane_line_seg.fp16")}
    write_json(sample_dir / "sample-index.json", {"schema_version": 1,
               "scope": "yolop-independent-diagnostic-sample", "payloads": payloads})


def _context(profile):
    names = ("service_instance", "acquisition_worker", "pipeline_instance", "capture_epoch",
             "sdk_bridge_epoch", "clock_epoch")
    result = {name: str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-yolop/" + name)) for name in names}
    result.update(device_id=profile["device_mxid"], startup_deadline_j_us="90000000",
                  graph_profile_sha256=hashlib.sha256(canonical(profile)).hexdigest(),
                  stream_instances={name: str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-stream/" + name))
                                    for name in ("rgb_video", "depth_right", "nn_input", "nn_output")})
    return result


def _manifest(samples):
    profile = {"schema_version": 1, "profile_id": "oak-yolop-rgb-depth-bench-v1", "nn_requirement": "REQUIRED",
               "jpeg_enabled": False, "device_mxid": "SYNTHETIC_OAK", "fps": 15,
               "asset_dir": "/nonexistent/synthetic-only/yolop-assets",
               "asset_manifest": "/nonexistent/synthetic-only/yolop-assets/asset-manifest.json",
               "max_bytes": 64 * 1024**2, "min_free_bytes": 8 * 1024**3, "startup_timeout_s": 90,
               "stall_timeout_s": 0.5, "close_timeout_s": 15, "sync_tolerance_us": 10000,
               "nn_first_timeout_s": 15, "nn_stall_timeout_s": 5, "diagnostic_samples": max(3, samples)}
    context = _context(profile)
    key = hashlib.sha256(canonical(context)).hexdigest()
    asset = {"asset_id": ASSET_ID, "blob_bytes": 15973184, "blob_sha256": BLOB_SHA,
             "manifest_canonical_sha256": "79ffbd74d457b00dc7e59872e3d8c16ae6f8b9a35faeaab9e0b2608600732f71",
             "files": deepcopy(_MODEL_FILES), "device_readback_verified": False, "on_device_inference_verified": False}
    preprocessing = {"coordinate_convention": "continuous image-edge coordinates",
                     "id": "yolop-letterbox-640x360-to320-v1", "padding_bgr": [0, 0, 0],
                     "padding_is_observation": False, "padding_ltrb": [0, 70, 0, 70],
                     "resized_size_wh": [320, 180], "scale_xy": [0.5, 0.5], "source_size_wh": [640, 360],
                     "source_to_nn": "u_nn=0.5*u_source; v_nn=0.5*v_source+70",
                     "status": "project integration choice; ImageManip pixel equivalence not yet measured",
                     "valid_content_roi_xyxy": [0, 70, 320, 250]}
    setup = {"device_mxid": profile["device_mxid"], "depthai_version": "2.29.0.0", "model_id": ASSET_ID,
             "nn_context_key": key, "nn_ready": False, "metric_ready": False, "capture_ready": False,
             "jpeg_enabled": False, "nn_requirement": "REQUIRED", "blob_registration_count": 1,
             "pipeline_start_count": 1, "depth_alignment": "RECTIFIED_RIGHT", "depth_unit": "millimeter",
             "timestamp_offset": "END", "model_residency_evidence": "host_verified_asset_and_graph_start_only",
             "model_asset": asset, "preprocessing": preprocessing,
             "nn": {"input_source": "same_CAM_A_video_as_rgb_video", "input_type": "BGR888p",
                    "input_size_wh": [320, 320], "passthrough_metadata_only": True,
                    "pixel_equivalence_verified": False, "inference_threads": 1,
                    "sdk_layer_golden_policy": "first_valid_packet_only"},
             "graph_declaration": {"asset": {k: asset[k] for k in ("asset_id", "blob_bytes", "blob_sha256")},
                                   "openvino_version": "Version.VERSION_2021_4", "device_config_readback": False,
                                   "declared_settings": {"fps_requested": 15,
                                       "rgb": {"size_wh": [640, 360], "socket": "CAM_A", "type": "NV12"},
                                       "depth": {"alignment": "RECTIFIED_RIGHT", "extended_disparity": False,
                                                 "lr_check": True, "size_wh": [640, 480], "subpixel": False,
                                                 "unit": "millimeter"},
                                       "nn": {"content_size_wh": [320, 180], "inference_threads": 1,
                                              "padding_bgr": [0, 0, 0], "padding_ltrb": [0, 70, 0, 70],
                                              "size_wh": [320, 320], "type": "BGR888p"}}},
             "sdk_blob_metadata": _compiler_metadata()}
    golden = {"performed": True, "matched": True, "accessor_calls": 3, "elements": 447400, "elapsed_us": 500,
              "comparison": "finite_FP16_values_exact_SDK_accessor_vs_owned_bytes",
              "layers": [{"name": name, "elements": count, "matched": True} for name, count in (
                  ("det_out", 37800), ("drive_area_seg", 204800), ("lane_line_seg", 204800))]}
    probe = NNPacket("nn_output", 0, 700000, 800000, 1000000, bytes(894848), _TENSORS, key, golden).descriptor()
    probe["validation_elapsed_us"] = 37
    return {"schema_version": 1, "scope": "yolop-independent-diagnostics", "not_capture_v1": True,
            "context": context, "profile": profile, "setup": setup, "first_nn_layout_probe": probe,
            "nn_context_key": key, "nn_ready": False, "metric_ready": False, "pixel_equivalence": "not_evaluated"}


def _sample(manifest, ordinal):
    sdk = 2_000_000 + ordinal * 100_000
    sequence = ordinal * 2 + 8
    device = sdk - 100000
    key = manifest["nn_context_key"]
    rgb = RawFrame("rgb_video", sequence, device, sdk, sdk + 80000, 640, 360, "NV12", bytes(345600), 30006)
    depth = RawFrame("depth_right", ordinal + 3, device - 725, sdk - 725, sdk + 70000,
                     640, 480, "RAW16_LE", bytes(614400), 30005)
    stamp = NNInputStamp("nn_input", sequence, device, sdk, sdk + 300000, 320, 320, "BGR888p", key)
    nn = NNPacket("nn_output", sequence, device, sdk, sdk + 301000, bytes(894848), _TENSORS, key)
    descriptor = nn.descriptor()
    descriptor["validation_elapsed_us"] = 37
    metadata = {"schema_version": 1, "scope": "yolop-independent-diagnostic-sample",
                "context": deepcopy(manifest["context"]), "nn_context_key": key,
                "rgb": rgb.descriptor(), "depth": depth.descriptor(), "nn_input": stamp.descriptor(),
                "nn_output": descriptor,
                "rgb_source_key": [manifest["context"]["stream_instances"]["rgb_video"], str(sequence), str(device)],
                "source_timestamp_delta_us": 0, "arrival_difference_us": 221000,
                "depth_candidate_sdk_delta_us": -725, "qualification": "timestamp_candidate_only; not metric",
                "metric_ready": False,
                "capture_clock": {"bridge_sample": {"j_before_us": str(sdk - 1000),
                                    "sdk_us": str(sdk - 990), "j_after_us": str(sdk - 980)},
                                  "sdk_host_timestamp_j_bracket_us": [str(sdk - 11), str(sdk + 11)],
                                  "oak_sync_error_bound_us": None, "readout_error_bound_us": None,
                                  "metric_time_qualified": False}}
    payloads = {"rgb.nv12": rgb.payload, "depth.raw16le": depth.payload, "nn-buffer.bin": nn.payload}
    payloads.update({name + ".fp16": nn.layer_bytes(name) for name in ("det_out", "drive_area_seg", "lane_line_seg")})
    return metadata, payloads


def make_dataset(path, samples=3, complete=True):
    """새 경로에만 합성 자료를 만든다. 입력 원본의 overwrite 용도가 아니다."""
    if type(samples) is not int or not 0 <= samples <= 8 or complete and samples == 0:
        raise ValueError("invalid synthetic sample count")
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    (path / "samples").mkdir()
    manifest = _manifest(samples)
    write_json(path / "bench-manifest.json", manifest)
    for ordinal in range(1, samples + 1):
        directory = path / "samples" / f"{ordinal:04d}"
        directory.mkdir()
        metadata, payloads = _sample(manifest, ordinal)
        for name, data in payloads.items():
            (directory / name).write_bytes(data)
        write_json(directory / "source.json", metadata)
        refresh_index(directory)
    written = sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    matched = max(samples, 10)
    counts = {"rgb_video": matched + 3, "depth_right": matched + 3, "nn_input": matched, "nn_output": matched}
    association = {"context_key": manifest["nn_context_key"], "matched": matched,
                   "received": {k: counts[k] for k in ("rgb_video", "nn_input", "nn_output")},
                   "dropped": {k: 0 for k in ("rgb_video", "nn_input", "nn_output")},
                   "pending": {"rgb_video": 3, "nn_input": 0, "nn_output": 0}}
    receipt = {"schema_version": 1, "scope": "yolop-rgb-native-depth-bench", "complete": complete,
               "context": deepcopy(manifest["context"]), "reason": "duration_elapsed" if complete else "interrupted",
               "exit_code": 0 if complete else 130, "error": None,
               "requested_duration_s": 3.0, "started_utc": "2026-11-07T00:00:00+00:00", "elapsed_s": 3.0,
               "output": "/synthetic/diagnostics", "process_exit_confirmed": True, "forced_termination": False,
               "worker_exit_code": 0, "worker_pid": 1234, "metric_ready": False, "nn_ready": False,
               "runtime_readiness": "not_evaluated", "fault_streams": [],
               "last_worker_status": {"kind": "final", "phase": "CLOSED", "error": None,
                   "reason": "duration_elapsed" if complete else "stopped", "device_close_confirmed": True,
                   "nn_ready": False, "metric_ready": False,
                   "observations": {"received_frames": {"rgb_video": matched + 3, "depth_right": matched + 3},
                                    "sequence_gaps": {"rgb_video": 0, "depth_right": 0},
                                    "unmatched_frames": {"rgb_video": 0, "depth_right": 0},
                                    "candidate_pairs": matched + 3,
                                    "bench_warmup_30_pairs": matched + 3 >= 30, "metric_ready": False},
                   "nn_bench": {"matched": matched, "received": counts, "age_rejected": {k: 0 for k in counts},
                                "association": association},
                   "recording": {"accepted_samples": samples, "saved_samples": samples, "dropped_samples": 0,
                                 "written_bytes": written, "written_frames": samples, "failure_samples_saved": 0,
                                 "pending_frames": 0, "writer_exit_confirmed": True, "error": None,
                                 "last_write_j_us": 3000000}},
               "output_reservation": {"ok": True, "process_exit_confirmed": True, "forced_termination": False}}
    write_json(path / "yolop-bench-result.json", receipt)
    return path
