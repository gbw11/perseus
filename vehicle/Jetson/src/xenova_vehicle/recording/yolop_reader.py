"""선택 YOLOP 진단 표본의 경로·소유 bytes·순서를 검사한다.

장치, SDK, 모델 파일, 운영 권한을 열지 않는다. 모든 indexed 표본을 먼저 검사하고
전달 직전에도 파일 identity와 해시를 확인한다. S02 metadata의 서명이나 픽셀 동등성은
증명하지 않으며, 실패 진단과 마지막 미완성 꼬리는 정상 NN 표본으로 전달하지 않는다.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import re
import stat

from .reader import DatasetError, _canonical, _is_link, _object, _stamp
from .yolop_contract import (YolopDiagnosticSample, decode_sample,
                             validate_manifest, validate_receipt)


_MIB = 1024**2
_MANIFEST = "bench-manifest.json"
_RESULT = "yolop-bench-result.json"
_PAYLOAD_LIMITS = {"rgb.nv12": 345600, "depth.raw16le": 614400,
                   "nn-buffer.bin": _MIB, "det_out.fp16": 75600,
                   "drive_area_seg.fp16": 409600, "lane_line_seg.fp16": 409600}
_FILE_LIMITS = {**_PAYLOAD_LIMITS, "source.json": 64 * 1024,
                "sample-index.json": 16 * 1024}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SAMPLE_NAME = re.compile(r"000[1-8]\Z")
_TEMP_NAME = re.compile(r"yolop-bench-result\.json\.tmp(?:\.[1-9][0-9]{0,19})?\Z")
_TOTAL_LIMIT = 32 * _MIB
_FILE_COUNT_LIMIT = 76  # root4 + 정상8*8 + 실패8; 디렉터리는 별도로 제한한다.


class IncompleteDiagnosticError(DatasetError):
    """손상은 없지만 완전 종료가 입증되지 않아 명시적 partial 허용이 필요하다."""


class YolopDiagnosticReader:
    """최대8개 독립 진단 표본을 읽는다. 입력에는 어떤 파일도 쓰지 않는다.

    validate는 전체 선택 집합의 preflight다. samples는 이를 자동 실행하고 소유 bytes와
    원 시각을 보존한 bundle을 반환한다. 공개 manifest/receipt 변경은 내부 검사에 영향이 없다.
    """

    def __init__(self, dataset: Path, *, allow_incomplete: bool = False):
        if type(allow_incomplete) is not bool:
            raise ValueError("allow_incomplete must be boolean")
        self.dataset = Path(dataset).expanduser().absolute()
        self.allow_incomplete = allow_incomplete
        self.manifest: dict = {}
        self.receipt: dict | None = None
        self._directories = {}
        self._pins = {}
        self._records = []
        self._parsed = None
        self._summary = None
        self._total_bytes = 0

    def _directory(self, relative):
        path = self.dataset / relative
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or _is_link(path, info):
            raise DatasetError(f"not a real diagnostic directory: {relative or '.'}")
        current = _stamp(info)
        if relative in self._directories and current != self._directories[relative]:
            raise DatasetError(f"diagnostic directory changed: {relative or '.'}")
        self._directories.setdefault(relative, current)

    @contextmanager
    def _directory_handle(self, relative):
        parts = Path(relative).parts if relative else ()
        relatives = [""] + [str(Path(*parts[:i])) for i in range(1, len(parts) + 1)]
        for name in relatives:
            self._directory(name)
        handles = []
        try:
            if os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY"):
                flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
                handle = os.open(self.dataset, flags)
                handles.append(handle)
                if _stamp(os.fstat(handle)) != self._directories[""]:
                    raise DatasetError("diagnostic root changed while opening")
                for part, name in zip(parts, relatives[1:]):
                    handle = os.open(part, flags, dir_fd=handle)
                    handles.append(handle)
                    if _stamp(os.fstat(handle)) != self._directories[name]:
                        raise DatasetError("diagnostic parent changed while opening")
                yield handle
                for handle, name in zip(handles, relatives):
                    if _stamp(os.fstat(handle)) != self._directories[name]:
                        raise DatasetError("diagnostic parent changed during read")
            else:
                yield None
            for name in relatives:
                self._directory(name)
        finally:
            for handle in reversed(handles):
                os.close(handle)

    def _scan(self, relative, limit):
        names = []
        with self._directory_handle(relative) as handle:
            with os.scandir(handle if handle is not None else self.dataset / relative) as entries:
                for entry in entries:
                    if len(names) >= limit:
                        raise DatasetError("diagnostic directory entry limit exceeded")
                    # entry.name은 OS가 반환한 한 경로 성분이다. 이후 whitelist로만 사용한다.
                    names.append(entry.name)
        return set(names)

    def _read(self, relative, limit, *, expected=None):
        path = Path(relative)
        parent = str(path.parent) if path.parent != Path(".") else ""
        flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0)
                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
        with self._directory_handle(parent) as handle:
            before = (self.dataset / path).lstat()
            if _is_link(self.dataset / path, before):
                raise DatasetError(f"diagnostic link rejected: {relative}")
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise DatasetError(f"not a single-link regular diagnostic file: {relative}")
            if before.st_size > limit:
                raise DatasetError(f"diagnostic file exceeds bound before open: {relative}")
            if handle is None:
                fd = os.open(self.dataset / path, flags)
            else:
                fd = os.open(path.name, flags, dir_fd=handle)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                stamp = _stamp(info)
                if stamp != _stamp(before):
                    raise DatasetError(f"diagnostic file changed while opening: {relative}")
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise DatasetError(f"not a single-link regular diagnostic file: {relative}")
                if info.st_size > limit or expected is not None and stamp != expected:
                    raise DatasetError(f"diagnostic file changed or exceeds bound: {relative}")
                data = source.read(limit + 1)
                if len(data) != info.st_size or len(data) > limit or stamp != _stamp(os.fstat(source.fileno())):
                    raise DatasetError(f"diagnostic file changed during read: {relative}")
            # 같은 FD만 읽으면 파일명 교체를 놓칠 수 있어 경로의 identity도 재확인한다.
            if stamp != _stamp((self.dataset / path).lstat()):
                raise DatasetError(f"diagnostic file replaced during read: {relative}")
        return data, stamp

    def _pin(self, relative, limit):
        data, stamp = self._read(relative, limit)
        if len(self._pins) >= _FILE_COUNT_LIMIT:
            raise DatasetError("diagnostic file count limit exceeded")
        self._total_bytes += len(data)
        if self._total_bytes > _TOTAL_LIMIT:
            raise DatasetError("diagnostic dataset exceeds 32MiB")
        self._pins[relative] = (stamp, hashlib.sha256(data).hexdigest(), limit)
        return data

    def _pinned(self, relative):
        stamp, digest, limit = self._pins[relative]
        data, _ = self._read(relative, limit, expected=stamp)
        if hashlib.sha256(data).hexdigest() != digest:
            raise DatasetError(f"diagnostic hash changed after preflight: {relative}")
        return data

    def _check_directories(self):
        # 파일이 전혀 없는 마지막 꼬리도 신규 entry/교체를 사용 직전에 확인한다.
        for relative in self._directories:
            self._directory(relative)

    def _normal_tail(self, data):
        """writer가 순서대로 쓴 마지막 파일만 짧을 수 있다. NN 길이는 실제 probe에서 읽는다."""
        order = (*_PAYLOAD_LIMITS, "source.json")
        if set(data) != set(order[:len(data)]):
            raise DatasetError("unindexed normal tail is not a writer-order prefix")
        probe = self._parsed["manifest"]["first_nn_layout_probe"]
        expected = {"rgb.nv12": _PAYLOAD_LIMITS["rgb.nv12"],
                    "depth.raw16le": _PAYLOAD_LIMITS["depth.raw16le"],
                    "nn-buffer.bin": probe["payload_length"]}
        expected.update({tensor["name"] + ".fp16": tensor["byte_length"] for tensor in probe["tensors"]})
        for position, name in enumerate(order[:len(data)]):
            if name == "source.json":
                continue  # 최종 metadata는 잘린 JSON일 수도 있어 해석하지 않는다.
            length = len(data[name])
            if length > expected[name] or position < len(data) - 1 and length != expected[name]:
                raise DatasetError("unindexed normal tail has a short nonfinal or oversized file")

    def _sample_files(self, directory, *, failure=False):
        names = self._scan(directory, 8)
        if names - set(_FILE_LIMITS):
            raise DatasetError("unexpected diagnostic sample filename")
        data = {name: self._pin(str(Path(directory) / name), _FILE_LIMITS[name])
                for name in sorted(names)}
        payloads = {name: value for name, value in data.items() if name in _PAYLOAD_LIMITS}
        if sum(map(len, payloads.values())) > 3 * _MIB:
            raise DatasetError("diagnostic sample payloads exceed 3MiB")
        indexed = "sample-index.json" in names
        if indexed:
            if "source.json" not in names or not failure and names != set(_FILE_LIMITS):
                raise DatasetError("indexed diagnostic sample has missing files")
            index = _object(data["sample-index.json"], "diagnostic sample index")
            if (set(index) != {"schema_version", "scope", "payloads"}
                    or type(index["schema_version"]) is not int or index["schema_version"] != 1
                    or index["scope"] != "yolop-independent-diagnostic-sample"
                    or not isinstance(index["payloads"], dict) or set(index["payloads"]) != set(payloads)):
                raise DatasetError("invalid diagnostic payload index")
            for name, value in index["payloads"].items():
                if (not isinstance(value, dict) or set(value) != {"bytes", "sha256"}
                        or type(value["bytes"]) is not int or value["bytes"] != len(payloads[name])
                        or type(value["sha256"]) is not str or not _SHA.fullmatch(value["sha256"])
                        or value["sha256"] != hashlib.sha256(payloads[name]).hexdigest()):
                    raise DatasetError("diagnostic payload length/hash mismatch")
            metadata = _object(data["source.json"], "diagnostic source")
        else:
            if not failure:
                self._normal_tail(data)
            metadata = None  # 종료 도중 쓴 source.json도 잘릴 수 있다. 정상 tensor로 해석하지 않는다.
        return indexed, metadata, payloads, sum(map(len, data.values()))

    def _bundle(self, directory, ordinal, *, pinned=False):
        if pinned:
            data = {name: self._pinned(str(Path(directory) / name)) for name in _FILE_LIMITS}
            metadata = _object(data["source.json"], "diagnostic source")
            payloads = {name: data[name] for name in _PAYLOAD_LIMITS}
        else:
            indexed, metadata, payloads, _ = self._sample_files(directory)
            if not indexed:
                return None
        return decode_sample(metadata, payloads, manifest=self._parsed, ordinal=ordinal)

    @staticmethod
    def _ordered(bundle, previous, depth_seen):
        if previous is not None:
            for name in ("rgb", "nn_input", "nn_output"):
                current, old = getattr(bundle, name), getattr(previous, name)
                if (current.sequence <= old.sequence or current.device_us <= old.device_us
                        or current.sdk_us <= old.sdk_us or current.received_j_us < old.received_j_us):
                    raise DatasetError("duplicate or reversed RGB/NN diagnostic source")
            if bundle.availability_j_us < previous.availability_j_us:
                raise DatasetError("reversed diagnostic availability")
        depth = bundle.depth
        descriptor = _canonical(depth.descriptor())
        digest = hashlib.sha256(depth.payload).hexdigest()
        identity = depth.sequence
        if previous is not None and identity < previous.depth.sequence:
            raise DatasetError("reversed reused depth diagnostic source")
        if identity in depth_seen:
            if depth_seen[identity] != (descriptor, digest):
                raise DatasetError("reused depth source changed descriptor/payload")
        else:
            if depth_seen:
                old = previous.depth
                if (depth.sequence <= old.sequence or depth.device_us <= old.device_us
                        or depth.sdk_us <= old.sdk_us or depth.received_j_us < old.received_j_us):
                    raise DatasetError("reversed depth diagnostic source")
            depth_seen[identity] = (descriptor, digest)

    def validate(self) -> dict:
        """전체 indexed 집합을 검사한다. incomplete 허용은 손상된 indexed 표본을 건너뛰지 않는다."""
        self._directories, self._pins, self._records = {}, {}, []
        self._summary, self._parsed, self._total_bytes = None, None, 0
        try:
            root = self._scan("", 6)
            temps = {name for name in root if _TEMP_NAME.fullmatch(name)}
            if (root - {_MANIFEST, _RESULT, "samples", "failure"} - temps
                    or not {_MANIFEST, "samples"} <= root or len(temps) > 2):
                raise DatasetError("unexpected or missing diagnostic root entry")
            raw = self._pin(_MANIFEST, 2 * _MIB)
            self.manifest = _object(raw, "YOLOP bench manifest")
            self._parsed = validate_manifest(deepcopy(self.manifest))
            self.receipt = _object(self._pin(_RESULT, 64 * 1024), "YOLOP bench result") if _RESULT in root else None
            for name in sorted(temps):
                self._pin(name, 64 * 1024)
            writer_bytes = len(raw)
            names = sorted(self._scan("samples", 8))
            if any(not _SAMPLE_NAME.fullmatch(name) for name in names) or names != [f"{i:04d}" for i in range(1, len(names) + 1)]:
                raise DatasetError("diagnostic sample names must be contiguous 0001..0008")
            previous, depth_seen = None, {}
            tail_count = tail_bytes = 0
            for ordinal, name in enumerate(names, 1):
                directory = str(Path("samples") / name)
                indexed, metadata, payloads, written = self._sample_files(directory)
                writer_bytes += written
                if not indexed:
                    if ordinal != len(names):
                        raise DatasetError("unindexed diagnostic sample is not the final tail")
                    tail_count, tail_bytes = 1, written
                    continue
                bundle = decode_sample(metadata, payloads, manifest=self._parsed, ordinal=ordinal)
                self._ordered(bundle, previous, depth_seen)
                previous = bundle
                self._records.append((directory, ordinal, bundle.availability_j_us))
            failure_count = failure_tail_count = failure_bytes = 0
            if "failure" in root:
                indexed, _, _, failure_bytes = self._sample_files("failure", failure=True)
                failure_count, failure_tail_count = int(indexed), int(not indexed)
                writer_bytes += failure_bytes
            result = validate_receipt(self._parsed, deepcopy(self.receipt),
                                      sample_count=len(self._records), failure_count=failure_count,
                                      total_written_bytes=writer_bytes, tail_count=tail_count + failure_tail_count,
                                      tail_bytes=tail_bytes + (failure_bytes if failure_tail_count else 0))
            incomplete = result["requires_allow_incomplete"]
            if (tail_count or failure_tail_count or temps) and not incomplete:
                raise DatasetError("complete diagnostic source contains an unfinished tail/temp")
            summary = {**result, "sample_count": len(self._records),
                       "input_kind": "REPLAY", "live_authority": False, "nn_ready": False, "metric_ready": False,
                       "selection_kind": "sparse_independent_samples", "continuous_nn_recording": False,
                       "normal_sample_limit": 8, "file_count": len(self._pins), "total_file_bytes": self._total_bytes,
                       "writer_file_bytes": writer_bytes, "unindexed_tail_count": tail_count,
                       "unindexed_tail_bytes": tail_bytes, "failure_indexed_count": failure_count,
                       "failure_unindexed_count": failure_tail_count, "failure_file_bytes": failure_bytes,
                       "receipt_temp_count": len(temps), "hashes_verified": True,
                       "metadata_qualification": "structural consistency only; no external signature",
                       "pixel_equivalence": "not_evaluated", "zero_valid_samples": not self._records,
                       "source_context": deepcopy(self._parsed["context"]),
                       "model_provenance": deepcopy(self._parsed["model_provenance"]),
                       "first_availability_j_us": str(self._records[0][2]) if self._records else None,
                       "last_availability_j_us": str(self._records[-1][2]) if self._records else None}
            if len(_canonical(summary)) > 64 * 1024:
                raise DatasetError("diagnostic validation summary exceeds 64KiB")
            self._check_directories()
            # indexed 손상 여부를 모두 확인한 뒤에만 incomplete를 별도 분류한다.
            if incomplete and not self.allow_incomplete:
                raise IncompleteDiagnosticError("incomplete YOLOP diagnostics require allow_incomplete")
            self._summary = summary
            return deepcopy(summary)
        except OSError as exc:
            raise DatasetError(f"diagnostic filesystem read failed: {type(exc).__name__}") from exc

    def samples(self) -> Iterator[YolopDiagnosticSample]:
        """전체 preflight와 현재 파일 재확인 뒤 표본마다 소유 bytes를 다시 읽어 전달한다."""
        if self._summary is None:
            self.validate()
        try:
            # validate와 첫 next 사이 바뀐 마지막 표본도 첫 소비 전에 거절한다.
            self._check_directories()
            for relative in self._pins:
                self._pinned(relative)
            for directory, ordinal, availability in self._records:
                self._check_directories()
                self._pinned(_MANIFEST)
                if _RESULT in self._pins:
                    self._pinned(_RESULT)
                bundle = self._bundle(directory, ordinal, pinned=True)
                if bundle.availability_j_us != availability:
                    raise DatasetError("diagnostic availability changed after preflight")
                yield bundle
        except OSError as exc:
            raise DatasetError(f"diagnostic filesystem read failed: {type(exc).__name__}") from exc
