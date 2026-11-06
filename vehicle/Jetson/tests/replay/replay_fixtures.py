"""실제 capture writer 형식의 작은 원본 기록. 카메라/SDK 없이 시험용으로만 만든다."""

import hashlib
import json
from pathlib import Path
import time
import uuid

from xenova_vehicle.capture_profile import CaptureProfile
from xenova_vehicle.oak.frames import RawFrame
from xenova_vehicle.recording.writer import CaptureWriter


def canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("ascii")


def create_dataset(path: Path, *, complete=True, pairs=2) -> Path:
    """version1 writer로 원본 bytes/index/manifest와 종료 receipt를 만든다."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=False)
    profile = CaptureProfile("REPLAY_TEST_DEVICE").to_dict()
    identity = lambda name: str(uuid.uuid5(uuid.NAMESPACE_URL, "xenova-replay-fixture/" + name))
    context = {name: identity(name) for name in ("service_instance", "acquisition_worker",
               "pipeline_instance", "capture_epoch", "sdk_bridge_epoch", "clock_epoch")}
    context.update(device_id=profile["device_mxid"],
                   graph_profile_sha256=hashlib.sha256(canonical(profile)).hexdigest(),
                   stream_instances={s: identity(s) for s in ("rgb_video", "depth_right")})
    manifest = {"schema_version": 1, "scope": "rgb-native-depth-bench", "context": context,
                "profile": profile, "setup": {"device_mxid": profile["device_mxid"],
                    "nn_requirement": "ABSENT", "jpeg_enabled": False, "fixture": True},
                "nn_requirement": "ABSENT", "metric_ready": False,
                "clock_qualification": "SDK-host/J bracket only; OAK error bound unvalidated"}
    writer = CaptureWriter(path, manifest, max_bytes=profile["max_bytes"], min_free_bytes=0)
    writer.start()
    for sequence in range(pairs):
        rgb_sdk, rgb_device = 1_000_000 + sequence * 66_667, 10_000 + sequence * 66_667
        for stream, delta, height, encoding, size in (
                ("rgb_video", 0, 360, "NV12", 345600),
                ("depth_right", 500, 480, "RAW16_LE", 614400)):
            received = rgb_sdk + 1000 + (100 if delta else 0)
            frame = RawFrame(stream, sequence, rgb_device + delta, rgb_sdk + delta,
                             received, 640, height, encoding,
                             bytes([sequence % 251]) * size, 1000 if not delta else None)
            before, after = received - 500, received - 480
            sample_sdk = before - 20
            metadata = {"capture_epoch": context["capture_epoch"],
                        "stream_instance": context["stream_instances"][stream],
                        "source_key": [context["stream_instances"][stream], str(sequence),
                                       str(frame.device_us)],
                        "graph_profile_sha256": context["graph_profile_sha256"],
                        "sdk_bridge_epoch": context["sdk_bridge_epoch"],
                        "clock_evidence": {
                            "bridge_sample": {"j_before_us": str(before), "sdk_us": str(sample_sdk),
                                              "j_after_us": str(after)},
                            "sdk_host_timestamp_j_bracket_us": [str(frame.sdk_us + 19),
                                                                 str(frame.sdk_us + 41)],
                            "oak_sync_error_bound_us": None, "readout_error_bound_us": None,
                            "metric_time_qualified": False},
                        "candidate_pair": None if not delta else {
                            "other_stream": "rgb_video", "other_sequence": str(sequence),
                            "other_device_timestamp_us": str(rgb_device),
                            "sdk_timestamp_delta_us": 500,
                            "qualification": "timestamp_candidate_only"}}
            deadline = time.monotonic() + 5
            while writer.snapshot()["pending_frames"] >= 8 and time.monotonic() < deadline:
                time.sleep(0.001)
            if not writer.submit(frame, metadata):
                raise RuntimeError("fixture writer dropped a frame")
    snapshot = writer.close(5)
    if snapshot["error"] or not snapshot["writer_exit_confirmed"]:
        raise RuntimeError(f"fixture recording failed: {snapshot}")
    receipt = {"schema_version": 1, "scope": "rgb-native-depth-bench", "context": context,
               "complete": complete, "exit_code": 0 if complete else 130,
               "reason": "duration_elapsed" if complete else "interrupted",
               "output": "/original-host/capture", "process_exit_confirmed": True,
               "worker_exit_code": 0, "forced_termination": False, "error": None,
               "metric_ready": False, "runtime_readiness": "not_evaluated",
               "last_worker_status": {"kind": "final", "phase": "CLOSED",
                   "device_close_confirmed": True, "error": None,
                   "reason": "duration_elapsed" if complete else "stopped",
                   "recording": snapshot,
                   "observations": {"received_frames": dict(snapshot["per_stream_counts"]),
                                    "candidate_pairs": pairs,
                                    "sequence_gaps": {"rgb_video": 0, "depth_right": 0}}}}
    (path / "capture-result.json").write_bytes(canonical(receipt) + b"\n")
    return path
