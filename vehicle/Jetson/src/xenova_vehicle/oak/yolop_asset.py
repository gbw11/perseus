"""기동에 사용할 고정 YOLOP bytes를 검증한다. 진단 스크립트·네트워크·Device는 열지 않는다.

파일 검사 후 같은 검증 bytes로 Blob을 만든다. host hash는 카메라 RAM의
readback이나 실제 NN 추론의 증거가 아니다.
"""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat


ASSET_ID = "yolop-320x320-rvc2-ov2021.4-sh6-v1"
BLOB_NAME = "yolop_320x320_openvino_2021.4_6shave.blob"
BLOB_SIZE = 15_973_184
BLOB_SHA256 = "8bc407f90814c6092ce0d76f7ce8cee0aaec68236358113946fe0134a655c437"
# S01의 승인 manifest 전체 의미를 고정한다. 포맷/키 순서만 달라도 같은 hash다.
MANIFEST_SHA256 = "79ffbd74d457b00dc7e59872e3d8c16ae6f8b9a35faeaab9e0b2608600732f71"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _unique(pairs: list[tuple[str, object]]) -> dict:
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate YOLOP manifest key: {key}")
        obj[key] = value
    return obj


def _constant(value: str) -> None:
    raise ValueError(f"nonfinite YOLOP manifest constant: {value}")


def _regular(path: Path, expected_size: int, *, retain: bool) -> tuple[bytes, str]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
        raise ValueError(f"YOLOP asset must be a regular file of pinned length: {path.name}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as source:
        opened = os.fstat(source.fileno())
        if (not stat.S_ISREG(opened.st_mode) or opened.st_size != expected_size
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError(f"YOLOP asset changed while opening: {path.name}")
        digest = hashlib.sha256()
        if retain:
            data = source.read(expected_size + 1)
            if len(data) != expected_size:
                raise ValueError(f"YOLOP asset length changed: {path.name}")
            digest.update(data)
        else:
            data = b""
            remaining = expected_size
            while remaining:
                chunk = source.read(min(65_536, remaining))
                if not chunk:
                    raise ValueError(f"YOLOP asset truncated: {path.name}")
                remaining -= len(chunk)
                digest.update(chunk)
            if source.read(1):
                raise ValueError(f"YOLOP asset grew: {path.name}")
        if os.fstat(source.fileno()).st_size != expected_size:
            raise ValueError(f"YOLOP asset length changed: {path.name}")
    return data, digest.hexdigest()


@dataclass(frozen=True)
class ValidatedYolopAsset:
    """한 기동 시도에서 검증한 immutable blob bytes와 manifest snapshot."""

    blob_bytes: bytes
    manifest: dict
    receipt: dict


def load_yolop_asset(asset_dir: Path, manifest_path: Path) -> ValidatedYolopAsset:
    """고정 manifest 및 다섯 자산 파일을 검사하고 blob만 bytes로 보유한다."""
    manifest_path = Path(manifest_path)
    info = manifest_path.lstat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 65_536:
        raise ValueError("YOLOP manifest must be a regular file of at most 64KiB")
    raw, _ = _regular(manifest_path, info.st_size, retain=True)
    try:
        manifest = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("invalid YOLOP manifest JSON") from exc
    canonical_sha = hashlib.sha256(_canonical(manifest)).hexdigest()
    if canonical_sha != MANIFEST_SHA256:
        raise ValueError("YOLOP manifest differs from the pinned S01 asset contract")
    if (manifest["schema_version"] != 1 or manifest["asset_id"] != ASSET_ID
            or manifest["compilation"]["requested_openvino_version"] != "2021.4"
            or manifest["compilation"]["requested_shaves"] != 6
            or manifest["compilation"]["observed_cmx_slices"] != 6):
        raise ValueError("unsupported YOLOP target/compiler contract")
    asset_dir = Path(asset_dir)
    if not asset_dir.is_dir():
        raise ValueError("YOLOP asset_dir is not a directory")
    checked = []
    blob_bytes = None
    for item in manifest["files"]:
        name = item["name"]
        if not name or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("YOLOP files must be plain basenames")
        retain = name == BLOB_NAME
        data, actual_sha = _regular(asset_dir / name, item["bytes"], retain=retain)
        if actual_sha != item["sha256"]:
            raise ValueError(f"YOLOP asset SHA256 mismatch: {name}")
        checked.append({"name": name, "bytes": item["bytes"], "sha256": actual_sha})
        if retain:
            if len(data) != BLOB_SIZE or actual_sha != BLOB_SHA256:
                raise ValueError("YOLOP blob differs from pinned bytes")
            blob_bytes = data
    if blob_bytes is None:
        raise ValueError("YOLOP compiled blob is absent")
    return ValidatedYolopAsset(blob_bytes, manifest,
                              {"asset_id": ASSET_ID, "manifest_canonical_sha256": canonical_sha,
                               "blob_sha256": BLOB_SHA256, "blob_bytes": BLOB_SIZE,
                               "files": checked, "device_readback_verified": False,
                               "on_device_inference_verified": False})


def validated_sdk_blob(dai, asset: ValidatedYolopAsset):
    """검증 bytes로만 SDK Blob을 만들고 고정 I/O metadata와 compiler 정보를 대조한다."""
    if dai.__version__ != "2.29.0.0":
        raise ValueError("YOLOP runtime requires DepthAI 2.29.0.0")
    # 2.29 pybind는 bytes constructor를 거절한다. 검증 bytes의 일시 list만 넘기고
    # 파일을 다시 읽지 않는다. 이 초기 할당(약128MiB refs)도 실측 peak RSS에 포함한다.
    values = list(asset.blob_bytes)
    try:
        blob = dai.OpenVINO.Blob(values)
    except Exception as exc:
        # pybind 오류의 원문은 대용량 인자를 repr할 수 있어 로그로 전파하지 않는다.
        raise ValueError(f"YOLOP Blob construction failed ({type(exc).__name__})") from None
    finally:
        del values

    def describe(tensor) -> dict:
        return {"name": tensor.name, "dataType": str(tensor.dataType),
                "order": str(tensor.order), "dims": list(tensor.dims),
                "strides": list(tensor.strides), "offset": tensor.offset}

    actual = {"sdk_inferred_version": str(blob.version), "num_shaves": blob.numShaves,
              "num_slices": blob.numSlices, "stage_count": blob.stageCount,
              "inputs": {name: describe(value) for name, value in blob.networkInputs.items()},
              "outputs": {name: describe(value) for name, value in blob.networkOutputs.items()}}
    expected = {key: value for key, value in asset.manifest["sdk_blob_metadata"].items()
                if key != "host_graph_required_version"}
    if actual != expected:
        raise ValueError("YOLOP SDK Blob metadata differs from pinned S01 metadata")
    return blob, actual
