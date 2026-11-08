"""Version1 원본 기록의 읽기·무결성 검사. SDK·장치·운영 통신을 열지 않는다.

전체 preflight 뒤에도 프레임을 읽을 때 길이·SHA256·파일 identity를 재검사한다.
partial 허용은 종료 증거/마지막 꼬리의 부재를 허용할 뿐 손상된 관측을 승인하지 않는다.
"""

from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

from ..capture_profile import CaptureProfile
from ..oak.frames import RawFrame

_STREAMS = ("rgb_video", "depth_right")
_MANIFEST_LIMIT = 2 * 1024**2
_RECEIPT_LIMIT = 64 * 1024
_INDEX_LIMIT = 64 * 1024**2
_LINE_LIMIT = 32 * 1024
_FRAME_LIMIT = 10_000
_TOTAL_LIMIT = 4 * 1024**3
_PAYLOAD_NAME = re.compile(r"[0-9]{8}-(rgb_video|depth_right)\.bin\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"0|[1-9][0-9]{0,19}\Z")


class DatasetError(ValueError):
    """기록의 형식·원 문맥·무결성 또는 안정된 파일 수명이 확인되지 않았다."""


@dataclass(frozen=True)
class ReplaySample:
    """소유한 원본 bytes와 원 index metadata 사본. 시각·순번을 재발급하지 않는다."""

    frame: RawFrame
    metadata: dict
    ordinal: int


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode("ascii")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DatasetError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise DatasetError(f"nonfinite JSON value: {value}")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise DatasetError("nonfinite JSON number")
    return number


def _object(data, label):
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                           parse_constant=_constant, parse_float=_float)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise DatasetError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"{label} must be a JSON object")
    return value


def _integer(value, label):
    if type(value) is not int or not 0 <= value < 2**64:
        raise DatasetError(f"invalid {label}")
    return value


def _u64(value, label):
    if type(value) is not str or not _DECIMAL.fullmatch(value) or int(value) >= 2**64:
        raise DatasetError(f"invalid decimal u64 {label}")
    return int(value)


def _uuid(value, label):
    try:
        valid = type(value) is str and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise DatasetError(f"invalid canonical UUID {label}")


def _stamp(info):
    # Windows Python의 lstat/fstat는 st_ctime 의미가 다를 수 있으므로 birthtime을 쓴다.
    change_time = getattr(info, "st_birthtime_ns", None) if os.name == "nt" else info.st_ctime_ns
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_mtime_ns, change_time)


def _is_link(path, info):
    # Windows junction/reparse point도 dataset 밖을 가리키는 우회 경로가 될 수 있다.
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & reparse)


class DatasetReader:
    """고정 벤치의 원본을 검증하고 index 순서대로 반환한다. 입력 경로에 쓰지 않는다."""

    def __init__(self, dataset: Path, *, allow_incomplete: bool = False,
                 expected_profile: dict | None = None):
        self.dataset = Path(dataset).expanduser().absolute()
        self.allow_incomplete = bool(allow_incomplete)
        self.expected_profile = deepcopy(expected_profile)
        self.manifest: dict = {}
        self.receipt: dict | None = None
        self._directories = {}
        self._pins = {}
        self._control_pins = {}
        self._records = []
        self._summary = None

    def _directory(self, relative):
        path = self.dataset / relative
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_link(path, info):
            raise DatasetError(f"dataset directory is not a real directory: {relative or '.'}")
        current = _stamp(info)
        if relative in self._directories and self._directories[relative] != current:
            raise DatasetError(f"dataset directory changed: {relative or '.'}")
        self._directories.setdefault(relative, current)

    @contextmanager
    def _open(self, relative, limit, expected=None):
        """regular file만 연다. POSIX는 directory FD와 NOFOLLOW로 부모 교체도 제한한다."""
        self._directory("")
        parts = Path(relative).parts
        if len(parts) == 2 and parts[0] == "frames":
            self._directory("frames")
        elif len(parts) != 1:
            raise DatasetError("invalid dataset file path")
        handles = []
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            if os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY"):
                directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
                directory = os.open(self.dataset, directory_flags)
                handles.append(directory)
                if _stamp(os.fstat(directory)) != self._directories[""]:
                    raise DatasetError("dataset root changed while opening")
                if len(parts) == 2:
                    directory = os.open("frames", directory_flags, dir_fd=directory)
                    handles.append(directory)
                    if _stamp(os.fstat(directory)) != self._directories["frames"]:
                        raise DatasetError("payload directory changed while opening")
                fd = os.open(parts[-1], flags, dir_fd=directory)
            else:
                path = self.dataset / relative
                before = path.lstat()
                if _is_link(path, before):
                    raise DatasetError(f"symlink payload/control file rejected: {relative}")
                fd = os.open(path, flags)
                if _stamp(before) != _stamp(os.fstat(fd)):
                    os.close(fd)
                    raise DatasetError(f"dataset file changed while opening: {relative}")
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                original = _stamp(info)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise DatasetError(f"not a single-link regular dataset file: {relative}")
                if info.st_size > limit:
                    raise DatasetError(f"dataset file exceeds limit: {relative}")
                if expected is not None and original != expected:
                    raise DatasetError(f"dataset file changed after validation: {relative}")
                yield source, info
                if original != _stamp(os.fstat(source.fileno())):
                    raise DatasetError(f"dataset file changed while reading: {relative}")
            self._directory("")
            if len(parts) == 2:
                self._directory("frames")
        finally:
            for handle in reversed(handles):
                os.close(handle)

    def _read(self, relative, limit, expected=None):
        with self._open(relative, limit, expected) as (source, info):
            data = source.read(limit + 1)
            if len(data) != info.st_size or len(data) > limit:
                raise DatasetError(f"dataset file length changed: {relative}")
            return data, _stamp(info)

    def _hash(self, relative, limit, expected=None):
        digest = hashlib.sha256()
        with self._open(relative, limit, expected) as (source, info):
            total = 0
            while block := source.read(64 * 1024):
                total += len(block)
                if total > limit:
                    raise DatasetError(f"dataset file exceeds limit: {relative}")
                digest.update(block)
            if total != info.st_size:
                raise DatasetError(f"dataset file length changed: {relative}")
            return digest.hexdigest(), _stamp(info)

    def _manifest(self):
        raw, stamp = self._read("manifest.json", _MANIFEST_LIMIT)
        self.manifest = _object(raw, "manifest")
        self._control_pins["manifest.json"] = (stamp, hashlib.sha256(raw).hexdigest(), _MANIFEST_LIMIT)
        m = self.manifest
        if type(m.get("schema_version")) is not int or m["schema_version"] != 1 \
                or m.get("scope") != "rgb-native-depth-bench":
            raise DatasetError("unsupported capture manifest version/scope")
        if m.get("nn_requirement") != "ABSENT" or m.get("metric_ready") is not False:
            raise DatasetError("unsupported capture manifest readiness/profile")
        if m.get("clock_qualification") != "SDK-host/J bracket only; OAK error bound unvalidated":
            raise DatasetError("unsupported capture clock qualification")
        fmt = m.get("recording_format")
        expected_format = {"version": 1, "index": "frames.jsonl", "payload_directory": "frames",
                           "payload_hash": "sha256", "summary_reserve_bytes": 65536,
                           "durability": "clean-close-no-power-loss-guarantee"}
        if not isinstance(fmt, dict) or _canonical(fmt) != _canonical(expected_format):
            raise DatasetError("unsupported recording format")
        profile = m.get("profile")
        if not isinstance(profile, dict):
            raise DatasetError("missing capture profile")
        try:
            fields = {k: profile[k] for k in CaptureProfile.__dataclass_fields__}
            rebuilt = CaptureProfile(**fields).to_dict()
        except (ValueError, TypeError, KeyError) as exc:
            raise DatasetError(f"invalid recorded capture profile: {exc}") from exc
        if _canonical(profile) != _canonical(rebuilt):
            raise DatasetError("recorded profile fields differ from version1")
        if self.expected_profile is not None and _canonical(profile) != _canonical(self.expected_profile):
            raise DatasetError("supplied profile differs from the recorded profile")
        context = m.get("context")
        names = ("service_instance", "acquisition_worker", "pipeline_instance", "capture_epoch",
                 "sdk_bridge_epoch", "clock_epoch")
        if not isinstance(context, dict) or set(context) != set(names) | {
                "stream_instances", "device_id", "graph_profile_sha256"}:
            raise DatasetError("invalid capture context fields")
        for name in names:
            _uuid(context[name], name)
        streams = context["stream_instances"]
        if not isinstance(streams, dict) or set(streams) != set(_STREAMS):
            raise DatasetError("invalid stream identity table")
        for name, identity in streams.items():
            _uuid(identity, name)
        if len(set(streams.values())) != 2:
            raise DatasetError("stream identities must be distinct")
        expected_hash = hashlib.sha256(_canonical(profile)).hexdigest()
        if context["graph_profile_sha256"] != expected_hash or context["device_id"] != profile["device_mxid"]:
            raise DatasetError("profile/context identity or hash mismatch")
        setup = m.get("setup")
        if not isinstance(setup, dict) or setup.get("device_mxid") != context["device_id"] \
                or setup.get("nn_requirement") != "ABSENT" or setup.get("jpeg_enabled") is not False:
            raise DatasetError("setup/context mismatch")
        return len(raw)

    def _receipt(self, reasons):
        try:
            raw, stamp = self._read("capture-result.json", _RECEIPT_LIMIT)
        except FileNotFoundError:
            self.receipt = None
            reasons.append("missing_capture_receipt")
            return
        self.receipt = _object(raw, "capture receipt")
        self._control_pins["capture-result.json"] = (stamp, hashlib.sha256(raw).hexdigest(), _RECEIPT_LIMIT)
        r = self.receipt
        if type(r.get("schema_version")) is not int or r["schema_version"] != 1 \
                or r.get("scope") != "rgb-native-depth-bench" or type(r.get("complete")) is not bool:
            raise DatasetError("unsupported capture receipt")
        if r.get("context") != self.manifest["context"]:
            raise DatasetError("receipt and manifest contexts differ")
        worker = r.get("last_worker_status")
        if worker is not None and not isinstance(worker, dict):
            raise DatasetError("invalid capture worker receipt")
        if worker and worker.get("recording") is not None and not isinstance(worker["recording"], dict):
            raise DatasetError("invalid recording counters")
        if not r["complete"]:
            reasons.append("capture_not_complete")

    def _row(self, row, ordinal):
        context = self.manifest["context"]
        stream = row.get("stream")
        if stream not in _STREAMS:
            raise DatasetError("unknown recorded stream")
        try:
            frame = RawFrame(stream, _u64(row["sequence"], "sequence"),
                             _u64(row["device_timestamp_us"], "device timestamp"),
                             _u64(row["sdk_timestamp_us"], "SDK timestamp"),
                             _u64(row["received_j_us"], "received J timestamp"),
                             row["width"], row["height"], row["encoding"], b"",
                             row["exposure_us"])
        except KeyError as exc:
            raise DatasetError(f"missing frame descriptor: {exc}") from exc
        expected_size = 345600 if stream == "rgb_video" else 614400
        expected_height = 360 if stream == "rgb_video" else 480
        # validate의 payload 사본은 최대614400B이며 전체 dataset을 RAM에 보관하지 않는다.
        descriptor = RawFrame(stream, frame.sequence, frame.device_us, frame.sdk_us,
                              frame.received_j_us, frame.width, frame.height, frame.encoding,
                              bytes(expected_size), frame.exposure_us).descriptor()
        fields = set(descriptor) | {"capture_epoch", "stream_instance", "source_key",
                  "graph_profile_sha256", "sdk_bridge_epoch", "clock_evidence",
                  "candidate_pair", "payload_file", "sha256"}
        if set(row) != fields:
            raise DatasetError("frame index fields differ from version1")
        if frame.width != 640 or frame.height != expected_height:
            raise DatasetError("frame dimensions differ from the bench profile")
        if any(k not in row for k in descriptor) or _canonical({k: row[k] for k in descriptor}) != _canonical(descriptor):
            raise DatasetError("frame encoding/shape/stride/unit/length mismatch")
        expected_file = f"frames/{ordinal:08d}-{stream}.bin"
        if row.get("payload_file") != expected_file:
            raise DatasetError("payload path or ordinal differs from the recording format")
        if type(row.get("sha256")) is not str or not _SHA.fullmatch(row["sha256"]):
            raise DatasetError("invalid payload SHA256")
        if (row.get("capture_epoch") != context["capture_epoch"]
                or row.get("stream_instance") != context["stream_instances"][stream]
                or row.get("sdk_bridge_epoch") != context["sdk_bridge_epoch"]
                or row.get("graph_profile_sha256") != context["graph_profile_sha256"]
                or row.get("source_key") != [context["stream_instances"][stream],
                                            str(frame.sequence), str(frame.device_us)]):
            raise DatasetError("frame source identity/context mismatch")
        self._clock(row.get("clock_evidence"), frame)
        pair = row.get("candidate_pair")
        if pair is not None:
            if not isinstance(pair, dict) or set(pair) != {"other_stream", "other_sequence",
                    "other_device_timestamp_us", "sdk_timestamp_delta_us", "qualification"}:
                raise DatasetError("invalid candidate pair fields")
            if (pair["other_stream"] not in _STREAMS or pair["other_stream"] == stream
                    or type(pair["sdk_timestamp_delta_us"]) is not int
                    or abs(pair["sdk_timestamp_delta_us"]) > self.manifest["profile"]["sync_tolerance_us"]
                    or pair["qualification"] != "timestamp_candidate_only"):
                raise DatasetError("invalid timestamp candidate pair")
            _u64(pair["other_sequence"], "candidate sequence")
            _u64(pair["other_device_timestamp_us"], "candidate device timestamp")
        return frame

    @staticmethod
    def _clock(value, frame):
        fields = {"bridge_sample", "sdk_host_timestamp_j_bracket_us", "oak_sync_error_bound_us",
                  "readout_error_bound_us", "metric_time_qualified"}
        if not isinstance(value, dict) or set(value) != fields \
                or value["metric_time_qualified"] is not False \
                or value["oak_sync_error_bound_us"] is not None or value["readout_error_bound_us"] is not None:
            raise DatasetError("invalid/unexpected clock qualification")
        sample = value["bridge_sample"]
        if not isinstance(sample, dict) or set(sample) != {"j_before_us", "sdk_us", "j_after_us"}:
            raise DatasetError("invalid SDK/J bridge sample")
        before, sdk, after = (_u64(sample[name], name) for name in ("j_before_us", "sdk_us", "j_after_us"))
        bracket = value["sdk_host_timestamp_j_bracket_us"]
        if not isinstance(bracket, list) or len(bracket) != 2:
            raise DatasetError("invalid SDK/J timestamp bracket")
        for bound in bracket:
            _u64(bound, "SDK/J timestamp bracket")
        expected = [str(frame.sdk_us + before - sdk - 1), str(frame.sdk_us + after - sdk + 1)]
        if before > after or not 0 <= frame.received_j_us - after <= 1_000_000 \
                or bracket != expected:
            raise DatasetError("invalid/expired SDK/J clock evidence")

    def _index(self):
        counts = dict.fromkeys(_STREAMS, 0)
        gaps = dict.fromkeys(_STREAMS, 0)
        previous = {}
        last_received = None
        recent = {stream: deque(maxlen=16) for stream in _STREAMS}
        stored_pairs = 0
        payload_bytes = 0
        discarded = 0
        with self._open("frames.jsonl", _INDEX_LIMIT) as (index, info):
            index_stamp = _stamp(info)
            digest = hashlib.sha256()
            while line := index.readline(_LINE_LIMIT + 1):
                digest.update(line)
                if len(line) > _LINE_LIMIT:
                    raise DatasetError("frame index line exceeds 32KiB")
                if not line.endswith(b"\n"):
                    discarded = len(line)
                    break
                if len(self._records) >= _FRAME_LIMIT:
                    raise DatasetError("frame count exceeds 10000")
                row = _object(line, "frame index line")
                ordinal = len(self._records) + 1
                frame = self._row(row, ordinal)
                if last_received is not None and frame.received_j_us < last_received:
                    raise DatasetError("global received J order moved backwards")
                last_received = frame.received_j_us
                old = previous.get(frame.stream)
                if old:
                    if frame.sequence <= old.sequence or frame.sdk_us <= old.sdk_us \
                            or frame.device_us <= old.device_us or frame.received_j_us < old.received_j_us:
                        raise DatasetError("per-stream sequence/timestamp did not increase")
                    gaps[frame.stream] += frame.sequence - old.sequence - 1
                previous[frame.stream] = frame
                counts[frame.stream] += 1
                name = row["payload_file"]
                actual_hash, payload_stamp = self._hash(name, row["payload_length"])
                if payload_stamp[4] != row["payload_length"] or actual_hash != row["sha256"]:
                    raise DatasetError(f"payload hash/length mismatch: {name}")
                self._pins[name] = payload_stamp
                payload_bytes += row["payload_length"]
                if payload_bytes > _TOTAL_LIMIT:
                    raise DatasetError("dataset payload exceeds 4GiB")
                pair = row.get("candidate_pair")
                if pair:
                    key = (pair["other_sequence"], pair["other_device_timestamp_us"])
                    for other_key, other_sdk in recent[pair["other_stream"]]:
                        if key == other_key and frame.sdk_us - other_sdk == pair["sdk_timestamp_delta_us"]:
                            stored_pairs += 1
                            break
                recent[frame.stream].append(((str(frame.sequence), str(frame.device_us)), frame.sdk_us))
                self._records.append(row)
            if index.tell() != info.st_size:
                raise DatasetError("index EOF differs from its declared size")
            self._control_pins["frames.jsonl"] = (index_stamp, digest.hexdigest(), _INDEX_LIMIT)
        return counts, gaps, payload_bytes, discarded, stored_pairs

    def _orphans(self):
        self._directory("frames")
        total = 0
        orphaned = []
        for count, path in enumerate((self.dataset / "frames").iterdir(), 1):
            if count > _FRAME_LIMIT:
                raise DatasetError("payload directory exceeds 10000 files")
            info = path.lstat()
            if not _PAYLOAD_NAME.fullmatch(path.name) or not stat.S_ISREG(info.st_mode) \
                    or info.st_nlink != 1 or _is_link(path, info):
                raise DatasetError("payload directory contains unsupported entry")
            total += info.st_size
            if total > _TOTAL_LIMIT:
                raise DatasetError("dataset files exceed 4GiB")
            relative = "frames/" + path.name
            if relative not in self._pins:
                orphaned.append(relative)
        return sorted(orphaned)

    def _closure(self, counts, logical_bytes, pairs, incomplete):
        r = self.receipt
        if not r or not r["complete"]:
            return
        worker = r.get("last_worker_status")
        if (r.get("exit_code") != 0 or type(r.get("exit_code")) is not int
                or r.get("worker_exit_code") != 0 or type(r.get("worker_exit_code")) is not int
                or r.get("process_exit_confirmed") is not True or r.get("forced_termination") is not False
                or r.get("error") is not None or r.get("reason") != "duration_elapsed"
                or not isinstance(worker, dict) or worker.get("device_close_confirmed") is not True
                or worker.get("error") is not None or worker.get("kind") != "final"
                or worker.get("phase") != "CLOSED"):
            raise DatasetError("complete receipt lacks confirmed normal closure")
        if r.get("metric_ready") is not False or r.get("runtime_readiness") != "not_evaluated":
            raise DatasetError("capture receipt has unexpected runtime readiness")
        recording = worker.get("recording")
        if not isinstance(recording, dict) or recording.get("writer_exit_confirmed") is not True \
                or recording.get("error") is not None:
            raise DatasetError("complete receipt lacks recording closure")
        expected = {"accepted_frames": len(self._records), "written_frames": len(self._records),
                    "pending_frames": 0, "queued_frames": 0, "per_stream_counts": counts,
                    "written_bytes": logical_bytes, "recorded_candidate_pairs": pairs}
        for name in ("accepted_frames", "written_frames", "pending_frames", "queued_frames",
                     "written_bytes", "recorded_candidate_pairs", "dropped_frames"):
            _integer(recording.get(name), "receipt " + name)
        if not 1 <= _integer(recording.get("queue_size"), "receipt queue_size") <= 8:
            raise DatasetError("invalid recording queue capacity")
        _integer(recording.get("last_write_j_us"), "receipt last_write_j_us")
        observations = worker.get("observations")
        if not isinstance(observations, dict) or not isinstance(observations.get("received_frames"), dict) \
                or set(observations["received_frames"]) != set(_STREAMS):
            raise DatasetError("missing received frame counters")
        received = observations["received_frames"]
        for stream in _STREAMS:
            _integer(received[stream], "received " + stream)
        candidates = _integer(observations.get("candidate_pairs"), "received candidate pairs")
        if sum(received.values()) != recording["accepted_frames"] + recording["dropped_frames"] \
                or candidates > min(received.values()):
            raise DatasetError("capture received/accepted/drop counters disagree")
        if not incomplete and (any(received[s] < counts[s] for s in _STREAMS) or candidates < pairs):
            raise DatasetError("capture received counters precede indexed data")
        if _canonical({k: recording.get(k) for k in expected}) != _canonical(expected):
            if not incomplete:
                raise DatasetError("complete receipt counters differ from indexed data")
            incomplete.append("receipt_counters_differ_from_recovered_prefix")
        if not incomplete and (not all(counts.values()) or pairs == 0):
            raise DatasetError("complete capture lacks both streams and stored timestamp candidates")

    def validate(self) -> dict:
        """모든 indexed payload의 SHA256까지 검사한다. 실패 뒤 frames는 사용할 수 없다."""
        self._summary = None
        self._records, self._pins, self._control_pins, self._directories = [], {}, {}, {}
        try:
            self._directory("")
            manifest_bytes = self._manifest()
            reasons = []
            self._receipt(reasons)
            counts, gaps, payload_bytes, tail, pairs = self._index()
            if tail:
                reasons.append("truncated_index_tail")
            orphans = self._orphans()
            if orphans:
                reasons.append("unindexed_payload_files")
            if not self._records:
                raise DatasetError("dataset contains no complete indexed frames")
            index_bytes = self._control_pins["frames.jsonl"][0][4]
            self._closure(counts, manifest_bytes + index_bytes + payload_bytes, pairs, reasons)
            if reasons and not self.allow_incomplete:
                raise DatasetError("incomplete recording requires --allow-incomplete: " + ", ".join(reasons))
            dropped = None
            if self.receipt:
                recording = (self.receipt.get("last_worker_status") or {}).get("recording", {})
                if isinstance(recording, dict) and type(recording.get("dropped_frames")) is int:
                    dropped = _integer(recording["dropped_frames"], "capture dropped frames")
            self._summary = {"source_complete": not reasons, "incomplete_reasons": reasons,
                             "frame_count": len(self._records), "per_stream_counts": counts,
                             "recorded_sequence_gaps": gaps, "orphan_payloads": orphans,
                             "discarded_tail_bytes": tail, "capture_dropped_frames": dropped,
                             "recorded_candidate_pairs": pairs, "payload_bytes": payload_bytes,
                             "hashes_verified": True, "input_kind": "REPLAY", "metric_ready": False}
            self._check_controls()
            return deepcopy(self._summary)
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
            self._summary = None
            if isinstance(exc, DatasetError):
                raise
            raise DatasetError(f"cannot validate dataset: {exc}") from exc

    def _check_controls(self):
        for name, (stamp, digest, limit) in self._control_pins.items():
            actual, _ = self._hash(name, limit, stamp)
            if actual != digest:
                raise DatasetError(f"dataset control file changed: {name}")
        self._directory("frames")

    def frames(self) -> Iterator[ReplaySample]:
        """원 index 순서로 소유 bytes를 읽는다. 원 시각/순번·metadata는 그대로다."""
        if self._summary is None:
            raise DatasetError("validate() must succeed before replay frames")
        try:
            self._check_controls()
            for ordinal, row in enumerate(self._records, 1):
                raw, _ = self._read(row["payload_file"], row["payload_length"], self._pins[row["payload_file"]])
                if len(raw) != row["payload_length"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                    raise DatasetError(f"payload changed during replay: {row['payload_file']}")
                frame = RawFrame(row["stream"], int(row["sequence"]), int(row["device_timestamp_us"]),
                                 int(row["sdk_timestamp_us"]), int(row["received_j_us"]),
                                 row["width"], row["height"], row["encoding"], raw, row["exposure_us"])
                frame.validate()
                yield ReplaySample(frame, deepcopy(row), ordinal)
            self._check_controls()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, DatasetError):
                raise
            raise DatasetError(f"cannot replay dataset: {exc}") from exc
