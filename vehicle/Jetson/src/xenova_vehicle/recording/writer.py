"""원본 관측의 유한 파일 기록. 파일 지연은 수신 스레드를 대기시키지 않는다.

파일·index는 한 non-daemon 스레드가 소유한다. 이는 프로세스 사이 IPC가 아니며,
최종 capture 성공 여부와 멈춘 writer를 포함한 프로세스 회수는 부모 런타임의 책임이다.
"""

from collections import deque
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import shutil
import threading
import time

from ..oak.frames import RawFrame

_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_METADATA_BYTES = 16 * 1024
_SUMMARY_RESERVE_BYTES = 64 * 1024


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"recording metadata is not finite JSON: {exc}") from exc


class CaptureWriter:
    """독점 예약한 새 output에 원본 bytes와 JSONL index를 기록한다.

    submit은 SDK 객체 대신 RawFrame의 소유한 bytes와 JSON metadata를 받는다.
    queue 포화는 False/drop counter, quota·파일 오류는 snapshot의 error로 보고한다.
    close는 유한 시간만 join하며 살아 있는 writer를 정상 종료로 표시하지 않는다.
    """

    def __init__(self, output: Path, manifest: dict, max_bytes: int,
                 min_free_bytes: int, queue_size: int = 8):
        self.output = Path(output)
        if not self.output.is_dir() or self.output.is_symlink():
            raise ValueError("capture output must be a reserved directory, not a symlink")
        if (type(max_bytes) is not int or max_bytes <= _SUMMARY_RESERVE_BYTES
                or type(min_free_bytes) is not int or min_free_bytes < 0
                or type(queue_size) is not int or not 1 <= queue_size <= 8):
            raise ValueError("invalid recording byte/queue limits")
        if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int \
                or manifest["schema_version"] != 1:
            raise ValueError("capture manifest requires schema_version 1")
        for name in ("frames", "manifest.json", "frames.jsonl"):
            if os.path.lexists(self.output / name):
                raise FileExistsError(f"refusing to overwrite capture entry: {name}")
        manifest_copy = dict(manifest)
        manifest_copy["recording_format"] = {
            "version": 1, "index": "frames.jsonl", "payload_directory": "frames",
            "payload_hash": "sha256", "summary_reserve_bytes": _SUMMARY_RESERVE_BYTES,
            "durability": "clean-close-no-power-loss-guarantee",
        }
        self._manifest_bytes = _json_bytes(manifest_copy) + b"\n"
        if len(self._manifest_bytes) > _MAX_MANIFEST_BYTES:
            raise ValueError("capture manifest exceeds 2 MiB")
        if len(self._manifest_bytes) + _SUMMARY_RESERVE_BYTES > max_bytes:
            raise ValueError("capture limit cannot hold manifest and summary reserve")
        self._max_bytes = max_bytes
        self._min_free_bytes = min_free_bytes
        self._queue: queue.Queue[tuple[RawFrame, bytes]] = queue.Queue(queue_size)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started = False
        self._closing = False
        self._accepted = self._written = self._dropped = self._written_bytes = 0
        self._last_write_j_us: int | None = None
        self._per_stream = {"rgb_video": 0, "depth_right": 0}
        self._recorded_candidate_pairs = 0
        self._recent_written = {stream: deque(maxlen=16) for stream in self._per_stream}
        self._error: str | None = None

    def start(self) -> None:
        """한 번만 writer를 시작한다. 파일 I/O 완료를 수신 경로에서 기다리지 않는다."""
        with self._lock:
            if self._started or self._closing:
                raise RuntimeError("capture writer already started or closed")
            self._started = True
            self._thread = threading.Thread(target=self._run, name="capture-writer", daemon=False)
        try:
            self._thread.start()
        except BaseException:
            with self._lock:
                self._closing = True
                self._error = "writer thread failed to start"
            raise

    def submit(self, frame: RawFrame, metadata: dict) -> bool:
        """원 metadata를 고정해 제출한다. 유효하지 않은 입력은 ValueError로 거절한다."""
        if not isinstance(frame, RawFrame) or not isinstance(metadata, dict):
            raise ValueError("recording requires RawFrame and dict metadata")
        descriptor = frame.descriptor()
        if any(not isinstance(key, str) for key in metadata):
            raise ValueError("recording metadata keys must be strings")
        if set(metadata) & (set(descriptor) | {"payload_file", "sha256"}):
            raise ValueError("metadata cannot replace frame or recording identity")
        metadata_bytes = _json_bytes(metadata)
        if len(metadata_bytes) > _MAX_METADATA_BYTES:
            raise ValueError("frame metadata exceeds 16 KiB")
        # 호출자의 중첩 dict가 뒤늦게 바뀌어도 원 관측 metadata는 바뀌지 않게 한다.
        record_bytes = _json_bytes({**descriptor, **json.loads(metadata_bytes)})
        with self._lock:
            if not self._started:
                raise RuntimeError("capture writer has not started")
            if self._closing or self._error is not None:
                self._dropped += 1
                return False
            try:
                self._queue.put_nowait((frame, record_bytes))
            except queue.Full:
                self._dropped += 1
                return False
            self._accepted += 1
            return True

    def snapshot(self) -> dict:
        """복사한 counter와 실제 thread 생존 상태를 반환한다. 성공 판정은 하지 않는다."""
        with self._lock:
            return {"accepted_frames": self._accepted, "written_frames": self._written,
                    "dropped_frames": self._dropped, "written_bytes": self._written_bytes,
                    "per_stream_counts": dict(self._per_stream), "error": self._error,
                    "queued_frames": self._queue.qsize(),
                    "pending_frames": self._accepted - self._written,
                    "queue_size": self._queue.maxsize, "last_write_j_us": self._last_write_j_us,
                    "recorded_candidate_pairs": self._recorded_candidate_pairs,
                    "writer_exit_confirmed": (self._thread is None or not self._thread.is_alive())}

    def close(self, timeout_s: float = 2) -> dict:
        """새 제출을 닫고 기존 queue를 drain한다. 시간 초과는 살아 있는 thread로 보고한다."""
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not math.isfinite(timeout_s) or timeout_s < 0):
            raise ValueError("writer close timeout must be finite and nonnegative")
        with self._lock:
            self._closing = True
        self._stop.set()
        if self._thread is not None and self._thread.ident is not None:
            self._thread.join(timeout_s)
        return self.snapshot()

    def _check_space(self, needed: int) -> None:
        if self._written_bytes + needed + _SUMMARY_RESERVE_BYTES > self._max_bytes:
            raise OSError("capture byte quota exhausted")
        free = shutil.disk_usage(self.output).free
        if free - needed - _SUMMARY_RESERVE_BYTES < self._min_free_bytes:
            raise OSError("capture free-space floor reached")

    def _write_bytes(self, destination, data: bytes) -> None:
        remaining = memoryview(data)
        while remaining:
            count = destination.write(remaining)
            if not count:
                raise OSError("capture write made no progress")
            with self._lock:
                self._written_bytes += count
            remaining = remaining[count:]

    def _write_frame(self, index, frame: RawFrame, record_bytes: bytes) -> None:
        ordinal = self._written + 1
        relative = f"frames/{ordinal:08d}-{frame.stream}.bin"
        record = {**json.loads(record_bytes), "payload_file": relative,
                  "sha256": hashlib.sha256(frame.payload).hexdigest()}
        line = _json_bytes(record) + b"\n"
        self._check_space(len(frame.payload) + len(line))
        with (self.output / relative).open("xb", buffering=0) as payload:
            self._write_bytes(payload, frame.payload)
        # 부분 payload가 완전한 관측으로 색인되지 않도록 원본을 먼저 닫는다.
        self._write_bytes(index, line)
        with self._lock:
            self._written += 1
            self._per_stream[frame.stream] += 1
            self._last_write_j_us = time.monotonic_ns() // 1000
            self._track_recorded_pair(frame, record)

    def _track_recorded_pair(self, frame: RawFrame, record: dict) -> None:
        """양쪽 payload/index가 쓰인 동기 후보만 센다. 최근 key는 stream당16개다."""
        epoch = record.get("capture_epoch")
        if not isinstance(epoch, str) or not epoch:
            return
        pair = record.get("candidate_pair")
        fields = {"other_stream", "other_sequence", "other_device_timestamp_us",
                  "sdk_timestamp_delta_us", "qualification"}
        if isinstance(pair, dict) and set(pair) == fields:
            other = pair["other_stream"]
            if (isinstance(other, str) and other in self._recent_written and other != frame.stream
                    and type(pair["sdk_timestamp_delta_us"]) is int
                    and pair["qualification"] == "timestamp_candidate_only"):
                key = (epoch, pair["other_sequence"], pair["other_device_timestamp_us"])
                for previous_key, previous_sdk_us in self._recent_written[other]:
                    if (key == previous_key
                            and frame.sdk_us - previous_sdk_us == pair["sdk_timestamp_delta_us"]):
                        self._recorded_candidate_pairs += 1
                        break
        key = (epoch, str(frame.sequence), str(frame.device_us))
        self._recent_written[frame.stream].append((key, frame.sdk_us))

    def _run(self) -> None:
        try:
            self._check_space(len(self._manifest_bytes))
            (self.output / "frames").mkdir(mode=0o700)
            with (self.output / "manifest.json").open("xb", buffering=0) as manifest:
                self._write_bytes(manifest, self._manifest_bytes)
            with (self.output / "frames.jsonl").open("xb", buffering=0) as index:
                while not self._stop.is_set() or not self._queue.empty():
                    try:
                        frame, record_bytes = self._queue.get(timeout=0.05)
                    except queue.Empty:
                        continue
                    try:
                        self._write_frame(index, frame, record_bytes)
                    finally:
                        self._queue.task_done()
                os.fsync(index.fileno())
        except Exception as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}: {exc}"[:512]
                self._closing = True
