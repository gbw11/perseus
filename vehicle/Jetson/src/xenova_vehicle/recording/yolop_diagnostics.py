"""상한 있는 YOLOP 독립 진단 표본. capture/replay version1 형식을 생성하지 않는다."""

import hashlib
import json
from pathlib import Path
import queue
import shutil
import threading
import time


def json_bytes(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("ascii") + b"\n"


class YolopDiagnosticWriter:
    """A의 writer1개, 정상 대기2개+실패 예약1개. submit은 파일 I/O를 기다리지 않는다."""

    def __init__(self, output: Path, manifest: dict, *, sample_limit: int,
                 max_bytes: int, min_free_bytes: int):
        self.output = Path(output)
        self.manifest = json_bytes(manifest)
        if len(self.manifest) > 2 * 1024**2:
            raise ValueError("YOLOP diagnostic manifest exceeds 2MiB")
        self.sample_limit = sample_limit
        self.max_bytes = max_bytes - 65536
        self.min_free_bytes = min_free_bytes
        self.pending = queue.Queue(maxsize=3)
        self.stop = threading.Event()
        self.guard = threading.Lock()
        self.accepted = self.saved = self.dropped = self.written_bytes = 0
        self.failure_accepted = self.failure_saved = 0
        self.last_write = 0
        self.error = None
        self.thread = threading.Thread(target=self._run, name="yolop-diagnostic-writer", daemon=False)

    def start(self):
        self.thread.start()

    def submit(self, metadata: dict, payloads: dict[str, bytes]) -> bool:
        """표본은 소유 bytes다. 설정 수를 채운 뒤 추가 표본은 저장하지 않는다."""
        return self._submit(metadata, payloads, failure=False)

    def submit_failure(self, metadata: dict, payloads: dict[str, bytes]) -> bool:
        """별도 실패 표본1개만 허용한다. 실패 bytes는 유효 tensor로 승인하지 않는다."""
        return self._submit(metadata, payloads, failure=True)

    def _submit(self, metadata, payloads, *, failure):
        if (self.stop.is_set() or self.error or (failure and self.failure_accepted)
                or not failure and self.accepted >= self.sample_limit):
            return False
        if not failure and self.pending.qsize() >= 2:
            self.dropped += 1
            return False
        if ((not payloads and not failure) or len(payloads) > 8 or sum(map(len, payloads.values())) > 3 * 1024**2
                or any(type(data) is not bytes for data in payloads.values())
                or any(not name or "/" in name or "\\" in name or name in (".", "..") for name in payloads)):
            raise ValueError("invalid diagnostic sample payloads")
        encoded = json_bytes(metadata)
        if len(encoded) > 64 * 1024:
            raise ValueError("diagnostic sample metadata exceeds 64KiB")
        try:
            self.pending.put_nowait((encoded, dict(payloads), failure))
        except queue.Full:
            self.dropped += 1
            return False
        if failure:
            self.failure_accepted += 1
        else:
            self.accepted += 1
        return True

    def _write(self, path, data):
        if self.written_bytes + len(data) > self.max_bytes:
            raise OSError("diagnostic quota exceeded")
        if shutil.disk_usage(self.output).free - len(data) < self.min_free_bytes:
            raise OSError("diagnostic free-space floor reached")
        with path.open("xb") as target:
            if target.write(data) != len(data):
                raise OSError("short diagnostic file write")
        with self.guard:
            self.written_bytes += len(data)
            self.last_write = time.monotonic_ns() // 1000

    def _run(self):
        try:
            samples = self.output / "samples"
            samples.mkdir(exist_ok=False)
            self._write(self.output / "bench-manifest.json", self.manifest)
            while not self.stop.is_set() or not self.pending.empty():
                try:
                    metadata, payloads, failure = self.pending.get(timeout=0.02)
                except queue.Empty:
                    continue
                directory = self.output / "failure" if failure else samples / f"{self.saved + 1:04d}"
                directory.mkdir(exist_ok=False)
                index = {"schema_version": 1, "scope": "yolop-independent-diagnostic-sample",
                         "payloads": {}}
                for name, data in payloads.items():
                    self._write(directory / name, data)
                    index["payloads"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                self._write(directory / "source.json", metadata)
                self._write(directory / "sample-index.json", json_bytes(index))
                with self.guard:
                    if failure:
                        self.failure_saved += 1
                    else:
                        self.saved += 1
                self.pending.task_done()
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"[:512]

    def snapshot(self):
        with self.guard:
            return {"accepted_samples": self.accepted, "saved_samples": self.saved,
                    "dropped_samples": self.dropped, "written_bytes": self.written_bytes,
                    "pending_frames": self.accepted + self.failure_accepted - self.saved - self.failure_saved,
                    "written_frames": self.saved + self.failure_saved, "last_write_j_us": self.last_write,
                    "failure_samples_saved": self.failure_saved,
                    "error": self.error, "writer_exit_confirmed": not self.thread.is_alive()}

    def close(self, timeout_s):
        self.stop.set()
        self.thread.join(max(0.0, timeout_s))
        return self.snapshot()
