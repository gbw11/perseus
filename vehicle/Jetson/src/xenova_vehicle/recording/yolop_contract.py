"""S02 진단 자료의 순수 계약 검사. 파일·모델·SDK·운영 통신을 열지 않는다.

원 기록 metadata와 재생 중 재검사 비용은 별개다. provenance의 자기 일관성을
검사하지만 외부 서명이나 카메라 RAM readback을 새로 입증하지 않는다.
"""

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
import math
from types import SimpleNamespace

from .reader import DatasetError, _u64, _uuid
from ..capture_profile import CaptureProfile
from ..oak.frames import RawFrame
from ..oak.observations import ClockEvidence
from ..oak.yolop_asset import ASSET_ID, BLOB_NAME, BLOB_SIZE, BLOB_SHA256, MANIFEST_SHA256
from ..oak.yolop_frames import (
    LAYER_SHAPES, MAX_NN_BYTES, NNInputStamp, NNPacket, RuntimeTensor,
    VALIDATION_ELEMENTS, VALIDATION_WORKSPACE_BYTES, _layouts,
)


PAYLOAD_NAMES = ("rgb.nv12", "depth.raw16le", "nn-buffer.bin",
                 "det_out.fp16", "drive_area_seg.fp16", "lane_line_seg.fp16")
_STREAMS = ("rgb_video", "depth_right", "nn_input", "nn_output")
_FILES = [
    {"name": "Luxonis-Model-Zoo-LICENSE.txt", "bytes": 1084,
     "sha256": "227c65da2a2303032fa8e53c8cac203b9bf7e7e2abbb86ac974101b2093f3b84"},
    {"name": "YOLOP-LICENSE.txt", "bytes": 1082,
     "sha256": "97acb244135bb2ed8ca578990bc91b670d61d0c5c37fc67b17bdc252d865cd9d"},
    {"name": "yolop_320x320.bin", "bytes": 15872074,
     "sha256": "43d8c3539605a0cc2e4be5b355c9c2ca0c3544683e1a725cf1071390375d084a"},
    {"name": "yolop_320x320.xml", "bytes": 582488,
     "sha256": "96586619ad12e863a0747172aa485d1ca7b1d55f9030bb00a7efffb80bfd3e6d"},
    {"name": BLOB_NAME, "bytes": BLOB_SIZE, "sha256": BLOB_SHA256},
]
_PREPROCESSING = {
    "coordinate_convention": "continuous image-edge coordinates",
    "id": "yolop-letterbox-640x360-to320-v1", "padding_bgr": [0, 0, 0],
    "padding_is_observation": False, "padding_ltrb": [0, 70, 0, 70],
    "resized_size_wh": [320, 180], "scale_xy": [0.5, 0.5],
    "source_size_wh": [640, 360],
    "source_to_nn": "u_nn=0.5*u_source; v_nn=0.5*v_source+70",
    "status": "project integration choice; ImageManip pixel equivalence not yet measured",
    "valid_content_roi_xyxy": [0, 70, 320, 250],
}
_FINITE_POLICIES = ("all_active_FP16LE_high_byte_translate", "all_active_FP16_values_u16_exponent_scan")
_GRAPH_SETTINGS = {
    "fps_requested": 15,
    "rgb": {"size_wh": [640, 360], "socket": "CAM_A", "type": "NV12"},
    "depth": {"alignment": "RECTIFIED_RIGHT", "extended_disparity": False, "lr_check": True,
              "size_wh": [640, 480], "subpixel": False, "unit": "millimeter"},
    "nn": {"content_size_wh": [320, 180], "inference_threads": 1, "padding_bgr": [0, 0, 0],
           "padding_ltrb": [0, 70, 0, 70], "size_wh": [320, 320], "type": "BGR888p"},
}


def _compiler_metadata():
    # compiler descriptor는 inner-first이며 strides가 비어 있다. runtime와 구분한다.
    def tensor(name, dtype, order, dims, offset=0):
        return {"name": name, "dataType": "DataType." + dtype, "order": "StorageOrder." + order,
                "dims": dims, "strides": [], "offset": offset}
    return {"sdk_inferred_version": "Version.VERSION_2021_4", "num_shaves": 6, "num_slices": 6,
            "stage_count": 295, "inputs": {"images": tensor("images", "U8F", "NCHW", [320, 320, 3, 1])},
            "outputs": {"det_out": tensor("det_out", "FP16", "CHW", [6, 6300, 1]),
                        "drive_area_seg": tensor("drive_area_seg", "FP16", "NCHW", [320, 320, 2, 1], 75648),
                        "lane_line_seg": tensor("lane_line_seg", "FP16", "NCHW", [320, 320, 2, 1], 485248)}}


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def _fail(message):
    raise DatasetError(message)


def _object(value, label):
    if type(value) is not dict:
        _fail(f"{label} must be an object")
    return value


def _int(value, label, *, maximum=2**64 - 1, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(f"invalid {label}")
    return value


def _equal(actual, expected, label):
    # JSON bool/int 동등 비교로 false/0 또는 true/1을 통과시키지 않는다.
    if _canonical(actual) != _canonical(expected):
        _fail(f"{label} differs from the recorded contract")


def _context(value, *, allow_initial=False):
    _object(value, "context")
    uuids = ("service_instance", "acquisition_worker", "pipeline_instance", "capture_epoch",
             "sdk_bridge_epoch", "clock_epoch")
    required = set(uuids) | {"device_id", "graph_profile_sha256", "stream_instances"}
    if not required <= value.keys() or set(value) - required - {"startup_deadline_j_us"}:
        _fail("invalid context fields")
    if not allow_initial and "startup_deadline_j_us" not in value:
        _fail("missing original startup deadline")
    for key in uuids:
        _uuid(value[key], key)
    streams = _object(value["stream_instances"], "stream instances")
    if set(streams) != set(_STREAMS):
        _fail("invalid named stream identities")
    for key, item in streams.items():
        _uuid(item, key)
    if len(set(streams.values())) != 4:
        _fail("stream identities alias")
    if "startup_deadline_j_us" in value:
        _u64(value["startup_deadline_j_us"], "startup deadline")
    return value


def _golden(value, *, required=False):
    if value is None and not required:
        return
    _object(value, "SDK golden")
    expected = {"performed": True, "matched": True, "accessor_calls": 3,
                "elements": VALIDATION_ELEMENTS,
                "comparison": "finite_FP16_values_exact_SDK_accessor_vs_owned_bytes"}
    if set(value) != set(expected) | {"elapsed_us", "layers"}:
        _fail("invalid SDK golden fields")
    for key, item in expected.items():
        _equal(value[key], item, "SDK golden " + key)
    _int(value["elapsed_us"], "SDK golden elapsed")
    _equal(value["layers"], [{"name": name, "elements": math.prod(shape), "matched": True}
                              for name, shape in LAYER_SHAPES.items()], "SDK golden layers")


def _nn_metadata(value, context_key):
    _object(value, "NN output descriptor")
    required = {"stream", "context_key", "sequence", "device_timestamp_us", "sdk_timestamp_us",
                "received_j_us", "payload_length", "tensors", "validation_elapsed_us",
                "validation_elements", "finite_validation", "compiler_layout_used", "golden", "metric_ready"}
    if set(value) - required - {"validation_workspace_bound_bytes"} or not required <= value.keys():
        _fail("invalid NN output descriptor fields")
    if (value["stream"] != "nn_output" or value["context_key"] != context_key
            or value["metric_ready"] is not False or value["compiler_layout_used"] is not False
            or value["finite_validation"] not in _FINITE_POLICIES):
        _fail("invalid NN output identity/qualification")
    for name in ("sequence", "device_timestamp_us", "sdk_timestamp_us", "received_j_us"):
        _u64(value[name], "NN " + name)
    length = _int(value["payload_length"], "NN payload length", maximum=MAX_NN_BYTES, minimum=1)
    _int(value["validation_elapsed_us"], "recorded NN validation elapsed")
    _equal(value["validation_elements"], VALIDATION_ELEMENTS, "recorded validation elements")
    if "validation_workspace_bound_bytes" in value:
        _equal(value["validation_workspace_bound_bytes"], VALIDATION_WORKSPACE_BYTES, "validation workspace")
    infos = value["tensors"]
    if type(infos) is not list or len(infos) != 3:
        _fail("NN requires three runtime tensor descriptors")
    tensors = []
    for info in infos:
        _object(info, "runtime tensor")
        if set(info) != {"name", "dataType", "data_type_code", "order", "order_code", "numDimensions",
                         "dims", "strides", "offset", "byte_length", "layout_origin"}:
            _fail("invalid runtime tensor descriptor fields")
        if (type(info["dims"]) is not list or type(info["strides"]) is not list
                or len(info["dims"]) > 4 or len(info["strides"]) > 4):
            _fail("invalid runtime tensor vector")
        tensor = RuntimeTensor(info["name"], info["data_type_code"], info["order_code"],
                               info["numDimensions"], tuple(info["dims"]), tuple(info["strides"]),
                               info["offset"], info["dataType"], info["order"])
        if (info["dataType"] != "DataType.FP16" or info["order"] != {
                3: "StorageOrder.C", 801: "StorageOrder.CHW", 17185: "StorageOrder.NCHW"}[tensor.order_code]):
            _fail("runtime tensor enum text/code mismatch")
        _equal(info, tensor.descriptor(), "runtime tensor")
        tensors.append(tensor)
    # Probe bytes는 저장되지 않았다. 여기서는 metadata span만 검사하고 원 finite
    # 또는 SDK golden 결과를 현재 bytes로 재검증했다는 주장을 하지 않는다.
    _layouts(SimpleNamespace(payload=bytes(length), tensors=tuple(tensors)))
    _golden(value["golden"])
    return tuple(tensors)


def validate_manifest(manifest: dict) -> dict:
    """원 JSON 경로 문자열을 그대로 hash한다. model 경로를 해석하거나 열지 않는다."""
    try:
        _object(manifest, "bench manifest")
        if set(manifest) != {"schema_version", "scope", "not_capture_v1", "context", "profile", "setup",
                             "first_nn_layout_probe", "nn_context_key", "nn_ready", "metric_ready", "pixel_equivalence"}:
            _fail("invalid YOLOP manifest fields")
        for key, expected in (("schema_version", 1), ("scope", "yolop-independent-diagnostics"),
                              ("not_capture_v1", True), ("nn_ready", False), ("metric_ready", False),
                              ("pixel_equivalence", "not_evaluated")):
            _equal(manifest[key], expected, "manifest " + key)
        context = _context(manifest["context"])
        profile = _object(manifest["profile"], "stored profile")
        fields = set(CaptureProfile.__dataclass_fields__) | {"asset_dir", "asset_manifest", "nn_first_timeout_s",
                  "nn_stall_timeout_s", "diagnostic_samples", "schema_version", "profile_id", "nn_requirement", "jpeg_enabled"}
        if set(profile) != fields:
            _fail("invalid stored YOLOP profile fields")
        for key, expected in (("schema_version", 1), ("profile_id", "oak-yolop-rgb-depth-bench-v1"),
                              ("nn_requirement", "REQUIRED"), ("jpeg_enabled", False)):
            _equal(profile[key], expected, "profile " + key)
        CaptureProfile(**{key: profile[key] for key in CaptureProfile.__dataclass_fields__})
        for key in ("asset_dir", "asset_manifest"):
            if type(profile[key]) is not str or not 0 < len(profile[key]) <= 4096 or "\x00" in profile[key]:
                _fail("invalid opaque model path")
        for key in ("nn_first_timeout_s", "nn_stall_timeout_s"):
            value = profile[key]
            if type(value) not in (float, int) or not math.isfinite(value) or not 0.1 <= value <= 15:
                _fail("invalid stored NN deadline")
        _int(profile["diagnostic_samples"], "diagnostic samples", maximum=8, minimum=1)
        if (context["device_id"] != profile["device_mxid"]
                or context["graph_profile_sha256"] != hashlib.sha256(_canonical(profile)).hexdigest()):
            _fail("stored profile/context digest mismatch")
        key = hashlib.sha256(_canonical(context)).hexdigest()
        if manifest["nn_context_key"] != key:
            _fail("NN context canonical digest mismatch")
        setup = _object(manifest["setup"], "setup")
        for name, expected in (("device_mxid", context["device_id"]), ("depthai_version", "2.29.0.0"),
                               ("model_id", ASSET_ID), ("nn_context_key", key), ("nn_ready", False),
                               ("metric_ready", False), ("capture_ready", False), ("jpeg_enabled", False),
                               ("nn_requirement", "REQUIRED"), ("blob_registration_count", 1),
                               ("pipeline_start_count", 1), ("depth_alignment", "RECTIFIED_RIGHT"),
                               ("depth_unit", "millimeter"), ("timestamp_offset", "END"),
                               ("model_residency_evidence", "host_verified_asset_and_graph_start_only")):
            _equal(setup.get(name), expected, "setup " + name)
        asset = _object(setup.get("model_asset"), "model provenance")
        _equal(asset, {"asset_id": ASSET_ID, "blob_bytes": BLOB_SIZE, "blob_sha256": BLOB_SHA256,
                       "manifest_canonical_sha256": MANIFEST_SHA256, "files": _FILES,
                       "device_readback_verified": False, "on_device_inference_verified": False}, "model provenance")
        _equal(setup.get("preprocessing"), _PREPROCESSING, "letterbox preprocessing")
        nn = _object(setup.get("nn"), "NN graph declaration")
        for name, expected in (("input_source", "same_CAM_A_video_as_rgb_video"), ("input_type", "BGR888p"),
                               ("input_size_wh", [320, 320]), ("passthrough_metadata_only", True),
                               ("pixel_equivalence_verified", False), ("inference_threads", 1),
                               ("sdk_layer_golden_policy", "first_valid_packet_only")):
            _equal(nn.get(name), expected, "NN " + name)
        graph = _object(setup.get("graph_declaration"), "graph declaration")
        _equal(graph.get("asset"), {"asset_id": ASSET_ID, "blob_bytes": BLOB_SIZE, "blob_sha256": BLOB_SHA256}, "graph asset")
        _equal(graph.get("openvino_version"), "Version.VERSION_2021_4", "graph OpenVINO")
        _equal(graph.get("device_config_readback"), False, "graph readback qualification")
        _equal(graph.get("declared_settings"), _GRAPH_SETTINGS, "graph declared settings")
        _equal(setup.get("sdk_blob_metadata"), _compiler_metadata(), "recorded compiler metadata")
        _nn_metadata(manifest["first_nn_layout_probe"], key)
        _golden(manifest["first_nn_layout_probe"]["golden"], required=True)
        return {"manifest": deepcopy(manifest), "context": deepcopy(context), "profile": deepcopy(profile),
                "context_key": key, "sync_tolerance_us": profile["sync_tolerance_us"],
                "diagnostic_samples": profile["diagnostic_samples"], "max_bytes": profile["max_bytes"],
                "model_provenance": deepcopy(asset), "preprocessing": deepcopy(_PREPROCESSING)}
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        if isinstance(error, DatasetError):
            raise
        raise DatasetError(f"invalid YOLOP manifest: {error}") from error


@dataclass(frozen=True)
class YolopDiagnosticSample:
    """선택된 원본 한 묶음. source metadata와 새 검사 packet의 비용은 별개다."""

    ordinal: int
    availability_j_us: int
    rgb: RawFrame
    depth: RawFrame
    nn_input: NNInputStamp
    nn_output: NNPacket
    metadata: dict
    payloads: dict[str, bytes]
    input_kind: str = field(default="REPLAY", init=False)
    live_authority: bool = field(default=False, init=False)
    nn_ready: bool = field(default=False, init=False)
    metric_ready: bool = field(default=False, init=False)
    depth_time_qualification: str = field(
        default="recorded_RGB_bridge_compatibility_only; original_depth_bridge_missing", init=False)


def _raw(info, payload, stream):
    _object(info, stream + " descriptor")
    result = RawFrame(stream, _u64(info["sequence"], "sequence"), _u64(info["device_timestamp_us"], "device stamp"),
                      _u64(info["sdk_timestamp_us"], "SDK stamp"), _u64(info["received_j_us"], "receive J"),
                      info["width"], info["height"], info["encoding"], payload, info["exposure_us"])
    result.validate()
    if (result.width, result.height) != (640, 360 if stream == "rgb_video" else 480):
        _fail("raw dimensions differ from the fixed bench graph")
    _equal(info, result.descriptor(), stream + " descriptor")
    return result


def decode_sample(metadata: dict, payloads: dict[str, bytes], *, manifest: dict,
                  ordinal: int) -> YolopDiagnosticSample:
    """검증한 파일 bytes를 원본 SDK와 독립된 타입으로 만든다. semantic decode는 하지 않는다."""
    try:
        _int(ordinal, "sample ordinal", minimum=1, maximum=manifest["diagnostic_samples"])
        if (type(payloads) is not dict or set(payloads) != set(PAYLOAD_NAMES)
                or any(type(item) is not bytes for item in payloads.values())
                or sum(map(len, payloads.values())) > 3 * 1024**2):
            _fail("invalid six diagnostic payloads")
        _object(metadata, "source metadata")
        if set(metadata) != {"schema_version", "scope", "context", "nn_context_key", "rgb", "depth", "nn_input",
                             "nn_output", "rgb_source_key", "capture_clock", "source_timestamp_delta_us",
                             "arrival_difference_us", "depth_candidate_sdk_delta_us", "qualification", "metric_ready"}:
            _fail("invalid diagnostic source fields")
        for key, expected in (("schema_version", 1), ("scope", "yolop-independent-diagnostic-sample"),
                              ("context", manifest["context"]), ("nn_context_key", manifest["context_key"]),
                              ("qualification", "timestamp_candidate_only; not metric"), ("metric_ready", False)):
            _equal(metadata[key], expected, "sample " + key)
        rgb = _raw(metadata["rgb"], payloads["rgb.nv12"], "rgb_video")
        depth = _raw(metadata["depth"], payloads["depth.raw16le"], "depth_right")
        info = _object(metadata["nn_input"], "NN input")
        stamp = NNInputStamp("nn_input", _u64(info["sequence"], "NN input sequence"),
                             _u64(info["device_timestamp_us"], "NN input device stamp"),
                             _u64(info["sdk_timestamp_us"], "NN input SDK stamp"),
                             _u64(info["received_j_us"], "NN input receive J"),
                             info["width"], info["height"], info["encoding"], info["context_key"])
        _equal(info, stamp.descriptor(), "metadata-only NN input")
        info = metadata["nn_output"]
        tensors = _nn_metadata(info, manifest["context_key"])
        packet = NNPacket("nn_output", _u64(info["sequence"], "NN sequence"),
                          _u64(info["device_timestamp_us"], "NN device stamp"),
                          _u64(info["sdk_timestamp_us"], "NN SDK stamp"),
                          _u64(info["received_j_us"], "NN receive J"), payloads["nn-buffer.bin"],
                          tensors, info["context_key"], golden=deepcopy(info["golden"]))
        _equal(info["payload_length"], len(packet.payload), "NN byte length")
        _equal(info["tensors"], manifest["manifest"]["first_nn_layout_probe"]["tensors"], "probe/runtime layout")
        for name in LAYER_SHAPES:
            if payloads[name + ".fp16"] != packet.layer_bytes(name):
                _fail("derived layer bytes differ from runtime raw slice: " + name)
        if (stamp.context_key != manifest["context_key"]
                or not (rgb.sequence == stamp.sequence == packet.sequence)
                or not (rgb.device_us == stamp.device_us == packet.device_us)
                or not (rgb.sdk_us == stamp.sdk_us == packet.sdk_us)):
            _fail("NN output/input/original RGB source identity mismatch")
        _equal(metadata["rgb_source_key"], [manifest["context"]["stream_instances"]["rgb_video"],
                                            str(rgb.sequence), str(rgb.device_us)], "RGB original source key")
        for key, expected in (("source_timestamp_delta_us", packet.sdk_us - stamp.sdk_us),
                              ("arrival_difference_us", packet.received_j_us - rgb.received_j_us),
                              ("depth_candidate_sdk_delta_us", depth.sdk_us - rgb.sdk_us)):
            _int(metadata[key], key, minimum=-(2**64 - 1))
            _equal(metadata[key], expected, key)
        if abs(depth.sdk_us - rgb.sdk_us) > manifest["sync_tolerance_us"]:
            _fail("depth is outside the recorded timestamp candidate tolerance")
        availability = max(item.received_j_us for item in (rgb, depth, stamp, packet))
        if (availability - rgb.received_j_us > 500_000
                or packet.received_j_us - rgb.received_j_us > 500_000):
            _fail("NN association exceeds the original RGB ring lifetime")
        clock = _object(metadata["capture_clock"], "capture clock")
        sample = _object(clock["bridge_sample"], "SDK/J bridge")
        bridge = ClockEvidence()
        bridge.update({key: _u64(value, "bridge " + key) for key, value in sample.items()})
        expected_clock = bridge.describe(rgb)
        _equal(clock, expected_clock, "original SDK/J bracket")
        lower = int(expected_clock["sdk_host_timestamp_j_bracket_us"][0])
        if any(not 0 <= item.received_j_us - lower <= 500_000 for item in (rgb, stamp, packet)):
            _fail("recorded RGB/NN source age is outside the original 500ms cap")
        # 동일 정수 us는 floor 양자화로 순서가 미정이다. strict SDK 증가만 쓴다.
        if (depth.sdk_us > _u64(sample["sdk_us"], "bridge SDK")
                and depth.received_j_us < _u64(sample["j_before_us"], "bridge J before")):
            _fail("depth receive time contradicts the recorded SDK/J causal order")
        # D의 원 bridge는 저장되지 않았다. 같은 기록 RGB bracket에 SDK delta를
        # 투영한 가설과 [0,500ms]가 양립하는지만 검사한다. 경계의 일부 교집합은
        # 허용하지만 실제 D bridge/freshness·clock 오차 수락의 증거는 아니다.
        delta = depth.sdk_us - rgb.sdk_us
        depth_lower = lower + delta
        depth_upper = int(expected_clock["sdk_host_timestamp_j_bracket_us"][1]) + delta
        implied_age_lower = depth.received_j_us - depth_upper
        implied_age_upper = depth.received_j_us - depth_lower
        if implied_age_upper < 0 or implied_age_lower > 500_000:
            _fail("depth receive time is incompatible with the recorded RGB bridge")
        return YolopDiagnosticSample(ordinal, availability, rgb, depth, stamp, packet,
                                     deepcopy(metadata), dict(payloads))
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        if isinstance(error, DatasetError):
            raise
        raise DatasetError(f"invalid YOLOP diagnostic sample: {error}") from error


def validate_receipt(manifest: dict, receipt: dict | None, *, sample_count: int,
                     failure_count: int, total_written_bytes: int, tail_count: int,
                     tail_bytes: int = 0) -> dict:
    """완료/중단/누락·stale 누계를 구분한다. partial은 indexed 손상을 허용하지 않는다.

    total_written_bytes는 manifest+samples+failure의 실제 파일 합이고 receipt/tmp는
    제외한다. tail_bytes는 index 없는 마지막 꼬리의 파일 합이다.
    """
    try:
        _int(sample_count, "sample count", maximum=manifest["diagnostic_samples"])
        _int(failure_count, "failure count", maximum=1)
        _int(tail_count, "partial tail count", maximum=1)
        _int(total_written_bytes, "writer file bytes", maximum=manifest["max_bytes"] - 65536)
        _int(tail_bytes, "tail bytes", maximum=total_written_bytes)
        if bool(tail_bytes) and not tail_count:
            _fail("tail bytes without a partial tail")
        summary = {"source_complete": False, "sample_count": sample_count,
                   "failure_count": failure_count, "tail_count": tail_count,
                   "requires_allow_incomplete": True, "receipt_status": "missing",
                   "writer_counter_qualification": "unavailable", "writer_dropped_samples": None,
                   "no_nn_yield": sample_count == 0, "continuous_nn_recording": False,
                   "selected_sample_limit": manifest["diagnostic_samples"],
                   "live_authority": False, "nn_ready": False, "metric_ready": False}
        if receipt is None:
            summary["classification"] = ("missing_receipt_prefix" if sample_count else
                                         "failure_only" if failure_count else "no_normal_samples")
            return summary
        _object(receipt, "bench receipt")
        for name, expected in (("schema_version", 1), ("scope", "yolop-rgb-native-depth-bench"),
                               ("metric_ready", False), ("runtime_readiness", "not_evaluated")):
            _equal(receipt.get(name), expected, "receipt " + name)
        if receipt.get("nn_ready", False) is not False or type(receipt.get("complete")) is not bool:
            _fail("invalid receipt readiness/completion")
        complete = receipt["complete"]
        code = _int(receipt.get("exit_code"), "receipt exit code", maximum=255)
        if complete != (code == 0):
            _fail("receipt completion/exit contradiction")
        context = _context(receipt.get("context"), allow_initial=not complete)
        expected = deepcopy(manifest["context"])
        if "startup_deadline_j_us" not in context and not complete:
            expected.pop("startup_deadline_j_us", None)
        _equal(context, expected, "receipt original context")
        for name in ("forced_termination", "process_exit_confirmed"):
            if type(receipt.get(name)) is not bool:
                _fail("invalid receipt process evidence")
        status = receipt.get("last_worker_status")
        authoritative = isinstance(status, dict) and status.get("kind") == "final"
        summary["receipt_status"] = "final" if authoritative else "stale_or_reservation"
        if complete and (not authoritative or receipt.get("reason") != "duration_elapsed"
                         or receipt.get("error") is not None or receipt["forced_termination"]
                         or not receipt["process_exit_confirmed"] or type(receipt.get("worker_exit_code")) is not int
                         or receipt["worker_exit_code"] != 0 or not sample_count or failure_count or tail_count):
            _fail("complete receipt lacks final close/reap/sample evidence")
        if status is not None:
            _object(status, "worker status")
            if status.get("kind") not in ("status", "final"):
                _fail("invalid worker status kind")
            for name in ("nn_ready", "metric_ready"):
                if status.get(name) is not False:
                    _fail("worker claims unsupported readiness")
            recording = _object(status.get("recording"), "writer counters")
            if recording:
                values = {name: _int(recording.get(name), "writer " + name) for name in (
                    "accepted_samples", "saved_samples", "dropped_samples", "written_bytes",
                    "pending_frames", "written_frames", "failure_samples_saved")}
                saved, accepted = values["saved_samples"], values["accepted_samples"]
                failures, pending = values["failure_samples_saved"], values["pending_frames"]
                if (not saved <= accepted <= manifest["diagnostic_samples"] or failures > 1
                        or values["written_frames"] != saved + failures
                        or not accepted - saved <= pending <= accepted - saved + 1 - failures
                        or values["written_bytes"] > total_written_bytes):
                    _fail("impossible writer counter relationship")
                if type(recording.get("writer_exit_confirmed")) is not bool:
                    _fail("invalid writer exit evidence")
                if authoritative:
                    if saved != sample_count or failures != failure_count:
                        _fail("final writer counts differ from indexed files")
                    if recording.get("error") is not None and tail_count:
                        if not total_written_bytes - tail_bytes <= values["written_bytes"] <= total_written_bytes:
                            _fail("failed writer byte counter excludes indexed prefix")
                        summary["writer_counter_qualification"] = "failed_unindexed_tail_byte_interval"
                    else:
                        if recording["writer_exit_confirmed"] and values["written_bytes"] != total_written_bytes:
                            _fail("final writer byte count differs from files")
                        summary["writer_counter_qualification"] = "final_exact" if recording["writer_exit_confirmed"] else "final_not_reaped"
                elif saved > sample_count or failures > failure_count:
                    _fail("stale status claims absent indexed samples")
                else:
                    summary["writer_counter_qualification"] = "stale_lower_bound"
                summary["writer_dropped_samples"] = values["dropped_samples"]
                if complete and (recording.get("error") is not None or not recording["writer_exit_confirmed"]
                                 or pending or accepted != saved):
                    _fail("complete receipt has unfinished/failed writer")
            elif authoritative or complete:
                _fail("final worker lacks writer counters")
            if complete and (status.get("phase") != "CLOSED" or status.get("device_close_confirmed") is not True
                             or status.get("error") is not None or status.get("reason") != "duration_elapsed"):
                _fail("complete receipt lacks actual device close")
            observations = status.get("observations")
            if observations is not None:
                _object(observations, "raw observation counters")
                for group in ("received_frames", "sequence_gaps", "unmatched_frames"):
                    vector = _object(observations.get(group), "observations " + group)
                    if set(vector) != {"rgb_video", "depth_right"}:
                        _fail("invalid raw observation stream counters")
                    for name, count in vector.items():
                        _int(count, group + " " + name)
                pairs = _int(observations.get("candidate_pairs"), "raw candidate pairs")
                raw_counts = observations["received_frames"]
                if (pairs > min(raw_counts.values())
                        or any(observations["unmatched_frames"][name] != raw_counts[name] - pairs
                               for name in raw_counts)):
                    _fail("raw observation counter denominator mismatch")
                _equal(observations.get("bench_warmup_30_pairs"), pairs >= 30, "capture warmup counter")
                _equal(observations.get("metric_ready"), False, "observation metric readiness")
                if authoritative and sample_count > raw_counts["rgb_video"]:
                    _fail("selected samples exceed original RGB observations")
            elif complete:
                _fail("complete receipt lacks raw observation counters")
            if "nn_bench" in status:
                bench = _object(status["nn_bench"], "NN bench counters")
                matched = _int(bench.get("matched"), "matched NN outputs")
                if authoritative and matched < sample_count:
                    _fail("more selected samples than matched inputs")
                if complete and not failure_count and values["accepted_samples"] + values["dropped_samples"] > matched:
                    _fail("normal writer submissions exceed matched NN inputs")
                for group in ("received", "age_rejected", "startup_received", "prestart_dropped"):
                    if group in bench:
                        vector = _object(bench[group], group)
                        if set(vector) != set(_STREAMS):
                            _fail("invalid NN stream counters")
                        for name, count in vector.items():
                            _int(count, group + " " + name)
                for name in ("validation_total_us", "validation_max_us", "validation_elements", "max_poll_us",
                             "max_steady_poll_us", "max_capture_age_upper_us", "capture_elapsed_us"):
                    if name in bench and bench[name] is not None:
                        _int(bench[name], "NN bench " + name)
                for name in ("arrival_difference_min_us", "arrival_difference_max_us"):
                    if name in bench and bench[name] is not None:
                        _int(bench[name], name, minimum=-(2**64 - 1))
                if "received" in bench and "age_rejected" in bench:
                    if any(bench["age_rejected"][name] > bench["received"][name] for name in _STREAMS):
                        _fail("age rejects exceed received counts")
                    if matched > min(bench["received"][name] for name in ("rgb_video", "nn_input", "nn_output")):
                        _fail("matches exceed original inputs")
                    if observations is not None and any(observations["received_frames"][name]
                            != bench["received"][name] - bench["age_rejected"][name]
                            for name in ("rgb_video", "depth_right")):
                        _fail("received raw observations disagree with age-qualified source counts")
                association = bench.get("association")
                if association is not None:
                    _object(association, "association counters")
                    _equal(association.get("context_key"), manifest["context_key"], "association context")
                    _equal(association.get("matched"), matched, "matched counter")
                    for name in ("validation_total_us", "validation_max_us", "validation_elements",
                                 "depth_frames_ignored", "retained_payload_bytes", "retained_payload_bound_bytes",
                                 "unmatched_nn_outputs", "ring_max_age_us", "nn_timeout_us", "capacity_per_stream"):
                        if name in association:
                            _int(association[name], "association " + name)
                    if "reasons" in association:
                        for count in _object(association["reasons"], "association drop reasons").values():
                            _int(count, "association drop reason count")
                    for name in ("rgb_video", "nn_input", "nn_output"):
                        received = _int(association["received"][name], "association received")
                        dropped = _int(association["dropped"][name], "association dropped")
                        queued = _int(association["pending"][name], "association pending", maximum=8)
                        if received != matched + dropped + queued:
                            _fail("association count denominator mismatch")
                if complete and not matched:
                    _fail("complete receipt has no matched NN output")
            elif complete:
                _fail("complete receipt lacks NN counts")
        summary.update(source_complete=complete, requires_allow_incomplete=not complete,
                       classification=("complete_selected_samples" if complete else
                                       "incomplete_selected_prefix" if sample_count else
                                       "failure_only" if failure_count else "no_normal_samples"))
        return summary
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        if isinstance(error, DatasetError):
            raise
        raise DatasetError(f"invalid YOLOP receipt: {error}") from error
