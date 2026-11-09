#!/usr/bin/env python3
"""구형 이미지용 guest-data relay만 포장한다. 설치·서비스·비밀 파일은 조작하지 않는다.

정규 이미지는 xenova-display recipe를 사용한다. 이 사본은 기존 feed/HDMI의
site-packages를 덮지 않도록 relay unit에만 PYTHONPATH를 공급한다.
"""
import argparse
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
ROUTER = ROOT / "platform/services/display/router"
DEPLOY = ROUTER / "deploy/guest-display"
UNIT = "xenova-display-guest-relay.service"
MODULES = ("__init__", "guest_frames", "mjpeg", "capture_limits", "mobile_upstream",
           "stream", "outputs", "runtime_status", "frame_presenter", "native_x11", "native_jpeg")


def package(output: Path):
    """새 디렉터리에 rootfs/manifest/tar를 만든다. 기존 산출물 덮어쓰기는 거절한다."""
    source = {name + ".py": (ROUTER / "xenova_display" / (name + ".py")).read_bytes()
              .replace(b"\r\n", b"\n") for name in MODULES}
    for name, data in source.items():
        tree = ast.parse(data, filename=name)
        compile(tree, name, "exec")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level:
                if node.level != 1 or node.module not in MODULES:
                    raise ValueError(f"relay closure is incomplete: {name}: {node.module}")
    unit = (DEPLOY / UNIT).read_bytes().replace(b"\r\n", b"\n")
    example = (DEPLOY / "guest-display-data-only.json.example").read_bytes().replace(b"\r\n", b"\n")
    digest = hashlib.sha256()
    for name, data in sorted({**source, UNIT: unit, "example": example}.items()):
        digest.update(name.encode() + b"\0" + data + b"\0")
    cohort = digest.hexdigest()
    runtime = "usr/lib/xenova-display-guest-relay/" + cohort[:16]
    files = {f"{runtime}/xenova_display/{name}": data for name, data in source.items()}
    files["usr/lib/systemd/system/" + UNIT] = unit
    files[f"etc/systemd/system/{UNIT}.d/10-standalone-runtime.conf"] = (
        "[Service]\nEnvironment=PYTHONPATH=/" + runtime + "\n").encode()
    files["usr/share/xenova/display/examples/guest-display-data-only.json.example"] = example
    output.mkdir(parents=True, exist_ok=False)
    stage = output / "rootfs"
    manifest = {"format": 1, "cohort_sha256": cohort, "runtime": "/" + runtime,
                "unit": UNIT, "files": []}
    for name, data in sorted(files.items()):
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o644)
        manifest["files"].append({"path": name, "size": len(data), "mode": "0644",
                                  "sha256": hashlib.sha256(data).hexdigest()})
    # Only an allowlisted payload, no active /etc/xenova configuration or tokens.
    with (output / "payload.tar.gz").open("wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name, data in sorted(files.items()):
                    entry = tarfile.TarInfo(name)
                    entry.size, entry.mode = len(data), 0o644
                    archive.addfile(entry, io.BytesIO(data))
    manifest["archive_sha256"] = hashlib.sha256((output / "payload.tar.gz").read_bytes()).hexdigest()
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = package(args.output)
    print(json.dumps({"cohort_sha256": result["cohort_sha256"], "files": len(result["files"])}))
