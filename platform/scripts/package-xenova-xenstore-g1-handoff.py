#!/usr/bin/env python3
"""검증된 G1 펌웨어와 같은 소스 기준의 플랫폼 런타임을 새 인계 경로에 게시한다."""
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "platform/first-install"))
from artifact_io import new_path, publish, regular, sha

PLATFORM = "platform/yocto/meta-xenova/recipes-core/xenova-platform/files"
MANIFEST = "handoff-manifest.json"
ARTIFACTS = {"zephyr.bin", "zephyr.elf", "zephyr.map", "zephyr.config", "zephyr.dts",
             "ZEPHYR-LICENSE", "XEN-ABI-LICENSE"}
DOCUMENTATION = (
    "docs/platform/build-guide.md", "docs/platform/cli-manager-spec.md",
    "platform/yocto/layers.lock", "platform/guests/sources.lock.json",
    "platform/guests/default-templates.lock.json", "platform/guests/zephyr/source.lock.json")
RUNBOOK = """# XenStore G1 인계물

G1 빌드·패키징과 시험 도구 사용은
[Xenova 빌드 가이드의 G1 절](docs/platform/build-guide.md#10-xenstore-g1-시험-인계물)을 따른다.
공통 VM 제어·신원·복구 계약은 [CLI·Manager 명세](docs/platform/cli-manager-spec.md)에 있다.
두 문서는 원래 저장소 경로로 동봉하며 빌드 가이드가 참조하는 고정 입력도 함께 보존한다.

- `firmware/`: 검증된 G1 펌웨어와 빌드 영수증
- `runtime/`: 같은 소스 기준의 독립 시험용 플랫폼 라이브러리
- `scripts/xenova-xenstore-g1-lab.py`: 소유 시험 도메인의 준비·관측·회수 도구
- `handoff-manifest.json`, `SHA256SUMS`: 문서를 포함한 전체 인계 파일의 해시

G1은 XenStore·VIF 관측 시험이며 Ethernet packet 송수신·DHCP·앱 준비의 통과를 뜻하지 않는다.
인계물 검증·복사만으로 보드 설치 경로나 기본 VM을 변경하지 않는다.
"""


def runtime_sources():
    """실제 /usr/lib/xenova 설치 레이아웃을 유지하며 지연 import 의존성도 포함한다."""
    result = {name + ".py": f"{PLATFORM}/{name}.py" for name in (
        "xenova_cpu", "xenova_vm", "xenova_runtime", "xenova_zephyr", "xenova_execution",
        "xenova_recovery", "xenova_resources", "xenova_zephyr_control",
        "xenova_zephyr_transition", "xenova_domain_identity", "xenova_catalog",
        "xenova_management", "xenova_management_cli", "xenova_first_boot", "xenova_wifi")}
    result.update({name + ".py": f"platform/first-install/{name}.py"
                   for name in ("artifact_io", "first_install_contract")})
    result.update({name + ".py": f"platform/guests/common/{name}.py"
                   for name in ("prepare_instance", "prepare_rootfs", "guest_identity", "xen_config")})
    result["update_guest_ssh.py"] = "platform/scripts/update-xenova-guest-ssh.py"
    for system in ("ubuntu", "debian"):
        result[f"xen-templates/{system}/xen.cfg.in"] = f"platform/guests/{system}/xen.cfg.in"
    return result


def relative(name):
    if not isinstance(name, str):
        raise ValueError("unsafe manifest path")
    path = PurePosixPath(name)
    if (path.is_absolute() or str(path) != name
            or not path.parts or any(p in (".", "..") for p in path.parts)
            or "\\" in name or "\n" in name):
        raise ValueError("unsafe manifest path")
    return name


def file_info(path):
    path = regular(path)
    return {"sha256": sha(path), "bytes": path.stat().st_size}


def source_inputs(source):
    base = source / "platform/guests/zephyr"
    return {str(p.relative_to(base)): file_info(p)
            for directory in ("xen-store", "xenstore-test-app")
            for p in sorted((base/directory).rglob("*")) if p.is_file() or p.is_symlink()}


def check_imports(source, mapping):
    """모듈 안의 지연 import까지 검사해 인계 묶음의 의존성 누락을 거부한다."""
    local = {Path(n).stem for n in mapping if n.endswith(".py")}
    for target, origin in mapping.items():
        if not target.endswith(".py"):
            continue
        tree = ast.parse(regular(source/origin).read_text(), filename=origin)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            missing = set(names) - local - sys.stdlib_module_names - {"__future__"}
            if missing:
                raise ValueError(f"unbundled runtime dependency in {target}: {sorted(missing)}")


def validate_firmware(source, firmware):
    manifest = json.loads(regular(firmware/"g1-build.json").read_text())
    if manifest.get("schema_version") != 1 or manifest.get("app") != "xenova-xenstore-g1":
        raise ValueError("G1 build manifest required")
    if set(manifest.get("files", {})) != ARTIFACTS:
        raise ValueError("incomplete G1 artifact set")
    for name in ARTIFACTS:
        if file_info(firmware/name) != manifest["files"][name]:
            raise ValueError("G1 firmware hash/size mismatch: " + name)
    inputs = source_inputs(source)
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if inputs != manifest.get("source_files") or digest != manifest.get("source_sha256"):
        raise ValueError("G1 firmware was built from different app/module sources")
    builder = source/"platform/scripts/build-xenova-xenstore-g1.py"
    if sha(regular(builder)) != manifest.get("build_script_sha256"):
        raise ValueError("G1 build script differs from firmware provenance")
    references = json.loads((source/"platform/guests/zephyr/xen-store/references.lock.json").read_text())
    if references != manifest.get("references"):
        raise ValueError("G1 reference lock differs from firmware provenance")
    return manifest


def verify(directory):
    """전체 파일 목록·해시·원소스 대응을 확인한다. Xen이나 장치를 호출하지 않는다."""
    if directory.is_symlink() or directory.resolve() != directory or not directory.is_dir():
        raise ValueError("canonical handoff directory required")
    manifest = json.loads(regular(directory/MANIFEST).read_text())
    if manifest.get("schema_version") != 1 or manifest.get("purpose") != "xenova-xenstore-g1-handoff":
        raise ValueError("G1 handoff manifest required")
    files = manifest["files"]
    actual = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ValueError("handoff contains a symlink")
        if path.is_file():
            actual.add(str(path.relative_to(directory)))
    if actual != set(files) | {MANIFEST, "SHA256SUMS"}:
        raise ValueError("handoff contains missing or unexpected files")
    for name, expected in files.items():
        if file_info(directory/relative(name)) != expected:
            raise ValueError("handoff hash/size mismatch: " + name)
    expected_sums = {name: info["sha256"] for name, info in files.items()}
    expected_sums[MANIFEST] = sha(directory/MANIFEST)
    lines = (directory/"SHA256SUMS").read_text().splitlines()
    expected_lines = [f"{digest}  {name}" for name, digest in sorted(expected_sums.items())]
    if lines != expected_lines:
        raise ValueError("handoff SHA256SUMS differs from manifest")
    for target, entry in manifest["runtime_sources"].items():
        if files["runtime/"+target] != entry["file"]:
            raise ValueError("runtime provenance differs from bundled file")
    g1 = json.loads((directory/"firmware/g1-build.json").read_text())
    for name in ARTIFACTS:
        if files["firmware/"+name] != g1["files"][name]:
            raise ValueError("firmware provenance differs from bundled file")
    return manifest


def package(source, firmware, output, *, execute=False):
    source, firmware, output = Path(source), Path(firmware), Path(output)
    for directory in (source, firmware):
        if not directory.is_absolute() or directory.resolve() != directory or directory.is_symlink() or not directory.is_dir():
            raise ValueError("canonical source/firmware directory required")
    new_path(output)
    if output.is_relative_to(source) or output.is_relative_to(firmware):
        raise ValueError("handoff output must be outside source and firmware")
    g1 = validate_firmware(source, firmware)
    mapping = runtime_sources()
    check_imports(source, mapping)
    origins = {"runtime/"+target: source/origin for target, origin in mapping.items()}
    origins.update({"firmware/"+name: firmware/name for name in ARTIFACTS | {"g1-build.json"}})
    origins["scripts/xenova-xenstore-g1-lab.py"] = source/"platform/scripts/xenova-xenstore-g1-lab.py"
    origins.update({name: source/name for name in DOCUMENTATION})
    files = {name: file_info(path) for name, path in origins.items()}
    lab_text = origins["scripts/xenova-xenstore-g1-lab.py"].read_text()
    tree = ast.parse(lab_text)
    cfg = next(ast.literal_eval(n.value) for n in tree.body
               if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "CFG" for t in n.targets))
    cfg = cfg.encode()
    generated = {"test.cfg.in": cfg, "RUNBOOK.md": RUNBOOK.encode()}
    files.update({name: {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
                  for name, content in generated.items()})
    if not execute:
        return {"action": "plan", "output": str(output), "files": len(files),
                "g1_source_sha256": g1["source_sha256"]}
    stage = output.parent/("."+output.name+"-preparing-"+os.urandom(16).hex())
    stage.mkdir(mode=0o700)
    try:
        for name, origin in origins.items():
            target = stage/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, target)
            target.chmod(0o444)
            if file_info(target) != files[name]:
                raise ValueError("input changed while copying: " + str(origin))
        for name, content in generated.items():
            (stage/name).write_bytes(content)
            (stage/name).chmod(0o444)
        if any(file_info(path) != files[name] for name, path in origins.items()):
            raise ValueError("handoff input changed before publication")
        validate_firmware(source, firmware)
        record = {"schema_version": 1, "purpose": "xenova-xenstore-g1-handoff",
                  "firmware_build_head": g1["repository_head"],
                  "packager_sha256": sha(regular(source/"platform/scripts/package-xenova-xenstore-g1-handoff.py")),
                  "g1_source_sha256": g1["source_sha256"], "files": files,
                  "runtime_sources": {target: {"source": origin, "file": files["runtime/"+target]}
                                      for target, origin in mapping.items()}}
        (stage/MANIFEST).write_text(json.dumps(record, indent=2)+"\n")
        sums = {name: entry["sha256"] for name, entry in files.items()}
        sums[MANIFEST] = sha(stage/MANIFEST)
        (stage/"SHA256SUMS").write_text("".join(f"{h}  {n}\n" for n, h in sorted(sums.items())))
        verify(stage)
        for path in stage.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
            elif path.is_dir():
                path.chmod(0o555)
        stage.chmod(0o555)
        publish(stage, output)
    except Exception:
        # 게시 실패의 전용 staging만 보존한다. 기존 인계물과 입력은 정리하지 않는다.
        print("handoff staging retained: " + str(stage), file=sys.stderr)
        raise
    return {"action": "published", "output": str(output), "files": len(files),
            "manifest_sha256": sha(output/MANIFEST)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    build = sub.add_parser("package")
    build.add_argument("--source-root", type=Path, default=ROOT)
    build.add_argument("--firmware", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--execute", action="store_true")
    audit = sub.add_parser("verify")
    audit.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.action == "verify":
        manifest = verify(args.directory)
        result = {"action": "verified", "files": len(manifest["files"]),
                  "manifest_sha256": sha(args.directory/MANIFEST)}
    else:
        result = package(args.source_root, args.firmware, args.output, execute=args.execute)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, StopIteration) as error:
        raise SystemExit(str(error))
