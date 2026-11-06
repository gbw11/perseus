"""YOLOP 자산의 로컬 해시·선택적 SDK metadata 검사. Device나 런타임을 열지 않는다."""

import argparse
import hashlib
import json
from pathlib import Path
import stat


DEFAULT_MANIFEST = (Path(__file__).resolve().parents[1] / "assets" / "manifests" /
                    "yolop-320x320-rvc2-ov2021.4-sh6.json")
REQUIRED_FILES = {
    "yolop_320x320_openvino_2021.4_6shave.blob": "compiled_blob",
    "yolop_320x320.xml": "openvino_ir_xml",
    "yolop_320x320.bin": "openvino_ir_weights",
    "YOLOP-LICENSE.txt": "upstream_license",
    "Luxonis-Model-Zoo-LICENSE.txt": "model_zoo_license",
}


def inspect_with_depthai(path: Path) -> dict:
    """설치된 고정 SDK로 blob과 host graph만 검사한다. Device 생성은 하지 않는다."""
    import depthai as dai

    if dai.__version__ != "2.29.0.0":
        raise ValueError(f"expected DepthAI 2.29.0.0, got {dai.__version__}")
    blob = dai.OpenVINO.Blob(path)

    def describe(value):
        return {"name": value.name, "dataType": str(value.dataType),
                "order": str(value.order), "dims": value.dims,
                "strides": value.strides, "offset": value.offset}

    pipeline = dai.Pipeline()
    pipeline.setOpenVINOVersion(dai.OpenVINO.Version.VERSION_2021_4)
    node = pipeline.create(dai.node.NeuralNetwork)
    node.setBlobPath(path)
    node.setNumInferenceThreads(1)
    required = str(pipeline.getRequiredOpenVINOVersion())
    if required != str(pipeline.getOpenVINOVersion()):
        raise ValueError("blob and configured host graph versions differ")
    return {"sdk_inferred_version": str(blob.version), "num_shaves": blob.numShaves,
            "num_slices": blob.numSlices, "stage_count": blob.stageCount,
            "inputs": {k: describe(v) for k, v in blob.networkInputs.items()},
            "outputs": {k: describe(v) for k, v in blob.networkOutputs.items()},
            "host_graph_required_version": required}


def verify(asset_dir: Path, manifest: dict, *, with_depthai=False) -> dict:
    """manifest에 고정한 파일들을 읽기만 한다. hash 실패 시 SDK 검사를 시작하지 않는다."""
    if manifest.get("schema_version") != 1 or manifest.get("asset_id") != \
            "yolop-320x320-rvc2-ov2021.4-sh6-v1":
        raise ValueError("unsupported YOLOP asset manifest")
    files = manifest["files"]
    if not isinstance(files, list) or not files or len(files) > 8:
        raise ValueError("invalid asset file table")
    if len(files) != len(REQUIRED_FILES) or {item["name"]: item["role"] for item in files} != REQUIRED_FILES:
        raise ValueError("asset manifest must include all five required files and roles")
    checked = []
    names = set()
    blob_path = None
    for item in files:
        name = item["name"]
        if (not isinstance(name, str) or not name or name in (".", "..")
                or "/" in name or "\\" in name or name in names):
            raise ValueError("asset filenames must be distinct plain basenames")
        names.add(name)
        path = asset_dir / name
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise ValueError(f"asset must be a regular file: {name}")
        if info.st_size != item["bytes"]:
            raise ValueError(f"asset length mismatch: {name}")
        with path.open("rb") as source:
            actual = hashlib.file_digest(source, "sha256").hexdigest()
        if actual != item["sha256"]:
            raise ValueError(f"asset SHA256 mismatch: {name}")
        checked.append({"name": name, "bytes": info.st_size, "sha256": actual})
        if item["role"] == "compiled_blob":
            if blob_path is not None:
                raise ValueError("multiple compiled blobs")
            blob_path = path
    if blob_path is None:
        raise ValueError("missing compiled blob")
    metadata = inspect_with_depthai(blob_path) if with_depthai else None
    if metadata is not None and metadata != manifest["sdk_blob_metadata"]:
        raise ValueError("DepthAI blob metadata differs from the pinned asset")
    return {"asset_id": manifest["asset_id"], "files": checked,
            "sdk_metadata": metadata, "device_created": False,
            "device_inference_verified": False, "exit_code": 0}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--with-depthai", action="store_true", help="parse blob with installed DepthAI 2.29")
    args = parser.parse_args()
    try:
        with args.manifest.open("rb") as source:
            raw = source.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError("asset manifest exceeds 1MiB")
        report = verify(args.asset_dir, json.loads(raw), with_depthai=args.with_depthai)
    except Exception as exc:
        report = {"exit_code": 1, "error": f"{type(exc).__name__}: {exc}",
                  "device_created": False, "device_inference_verified": False}
    print(json.dumps(report, ensure_ascii=True, allow_nan=False))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
