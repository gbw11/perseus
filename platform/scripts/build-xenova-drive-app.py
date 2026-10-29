#!/usr/bin/env python3
"""Snapshot/build/package an existing drive app with the frozen Xen network profile.

No broker secret, VM identity, host mutation, image assembly or catalog overwrite.
The candidate's source digest includes common runtime/Diagnostics/Publisher code.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "platform/guests/zephyr"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(app):
    groups = [f"platform/apps/{app}/zephyr", "platform/apps/common/xenova-mqtt-c",
              "platform/apps/common/xenova-drive-runtime"]
    groups += ["platform/guests/zephyr/" + name for name in ("xen-store", "xen-runtime", "diagnostics", "telemetry")]
    if app == "drive":
        groups += ["platform/apps/autonomous/zephyr/src", "platform/apps/manual/zephyr/src"]
    # Interpreter caches are generated output, never firmware source inputs.
    paths = [p for group in groups for p in sorted((ROOT / group).rglob("*"))
             if p.is_file() and "__pycache__" not in p.parts and p.suffix not in (".pyc", ".pyo")]
    paths += [ROOT / name for name in (
        "platform/scripts/build-xenova-drive-app.py", "platform/scripts/package-xenova-firmware.py",
        "platform/scripts/build-xenova-network-starter.py", "platform/guests/zephyr/network-xen.cfg.in",
        "platform/guests/zephyr/starter.lock.json", "platform/first-install/artifact_io.py",
        "platform/first-install/first_install_contract.py",
        "platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_zephyr.py",
        "platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_runtime.py",
        "platform/yocto/meta-xenova/recipes-core/xenova-platform/files/xenova_trace.py")]
    if any(p.is_symlink() for p in paths):
        raise ValueError("source snapshot must not contain linked inputs")
    return {str(p.relative_to(ROOT)): {"sha256": sha(p), "bytes": p.stat().st_size} for p in paths}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--app", choices=("manual", "autonomous", "drive"), required=True)
    p.add_argument("--profile", choices=("operations", "experiment"), default="operations",
                   help="operations excludes raw capture; experiment explicitly includes it")
    p.add_argument("--zephyr-source", type=Path, required=True)
    p.add_argument("--tools", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--execute", action="store_true")
    args = p.parse_args()
    for path in (args.zephyr_source, args.tools, args.output):
        if not path.is_absolute() or path.resolve() != path or path.is_symlink():
            raise ValueError("canonical absolute paths required")
    if args.output.exists() or any(args.output.is_relative_to(path) for path in (ROOT, args.zephyr_source, args.tools)):
        raise ValueError("new output outside repository/source/toolchain required")
    lock = json.loads((BASE / "starter.lock.json").read_text())
    commit = subprocess.check_output(["git", "-C", str(args.zephyr_source), "rev-parse", "HEAD"], text=True).strip()
    if commit != lock["zephyr_commit"]:
        raise ValueError("frozen Zephyr commit required")
    api_lock = json.loads((BASE / "xen-store/references.lock.json").read_text())
    for name, digest in api_lock["zephyr"]["api_files"].items():
        if sha(args.zephyr_source / name) != digest:
            raise ValueError("frozen Zephyr API differs: " + name)
    cmake = args.tools / "python/bin/cmake"
    ninja = args.tools / "python/bin/ninja"
    compiler = args.tools / "aarch64-zephyr-elf/bin/aarch64-zephyr-elf-gcc"
    if not all(path.is_file() for path in (cmake, ninja, compiler)):
        raise ValueError("existing fixed CMake/Ninja/AArch64 toolchain required")
    source = fingerprint(args.app)
    digest = hashlib.sha256(json.dumps({"profile": args.profile, "files": source},
                                      sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    print(json.dumps({"action": "build-drive-candidate" if args.execute else "plan", "app": args.app,
                      "source_sha256": digest, "profile": args.profile,
                      "output": str(args.output), "board_tested": False}), flush=True)
    if not args.execute:
        return
    args.output.mkdir(mode=0o755)
    snapshot = args.output / "input"
    for name in source:
        target = snapshot / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
        if sha(target) != source[name]["sha256"]:
            raise ValueError("source changed during snapshot: " + name)
    home = snapshot / f"platform/apps/{args.app}/zephyr"
    build = args.output / "build"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(args.tools / "python"),
               ZEPHYR_BASE=str(args.zephyr_source), ZEPHYR_TOOLCHAIN_VARIANT="cross-compile",
               CROSS_COMPILE=str(args.tools / "aarch64-zephyr-elf/bin/aarch64-zephyr-elf-"),
               PATH=str(args.tools / "python/bin") + ":" + os.environ["PATH"])

    def run(label, argv, *, cwd=None):
        with (args.output / (label + ".stdout")).open("wb") as stream:
            result = subprocess.run(list(map(str, argv)), env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=300, cwd=cwd)
        (args.output / (label + ".command.json")).write_text(json.dumps(
            {"argv": list(map(str, argv)), "rc": result.returncode, "timeout_s": 300}, indent=2) + "\n")
        if result.returncode:
            raise RuntimeError(label + " failed; full output preserved")

    profile_args = ["-DCONFIG_XENOVA_DRIVE_RECORDER=" + ("y" if args.profile == "experiment" else "n"),
                    "-DCONFIG_XENOVA_DIAGNOSTICS_THREADS=" + ("y" if args.profile == "experiment" else "n")]
    if args.app == "drive":
        profile_args = ["-DEXTRA_CONF_FILE=" + str(home / "profiles" / (args.profile + ".conf"))]
    run("configure", [cmake, "-S", home, "-B", build, "-GNinja", "-DBOARD=xenvm",
                      "-DCMAKE_MAKE_PROGRAM=" + str(ninja), "-DDTC_OVERLAY_FILE=" + str(home / "boards/xenvm.overlay"),
                      *profile_args])
    run("compile", [cmake, "--build", build, "-j", "4"])

    # .send may run on the kernel's small TCP workqueue, rather than app main.
    # Audit its direct frame with the same fixed compiler/config, not a host estimate.
    stack_dir = args.output / "stack-audit"
    stack_dir.mkdir()
    commands = json.loads((build / "compile_commands.json").read_text())
    command = next(v for v in commands if v["file"].endswith("/xen-runtime/src/netfront.c"))
    audit_args = shlex.split(command["command"])
    if set(audit_args) & {"-MF", "-MD", "-MMD"}:
        raise ValueError("stack audit must not overwrite original dependency output")
    audit_args[audit_args.index("-o") + 1] = str(stack_dir / "netfront.o")
    run("stack-audit", audit_args + ["-fstack-usage"], cwd=command["directory"])
    rows = [v.split("\t") for v in (stack_dir / "netfront.su").read_text().splitlines()]
    send = [v for v in rows if len(v) == 3 and v[0].endswith(":send")]
    if len(send) != 1 or send[0][2] != "static" or int(send[0][1]) > 256:
        raise ValueError("netfront send exceeds the direct 256-byte stack-frame budget")
    config = (build / "zephyr/.config").read_text()
    for name in ("XENOVA_DRIVE_RECORDER", "XENOVA_DIAGNOSTICS_THREADS"):
        if ("CONFIG_" + name + "=y\n" in config) != (args.profile == "experiment"):
            raise ValueError("resolved configuration differs from selected profile: " + name)
    if "CONFIG_STACK_SENTINEL=y\n" not in config:
        raise ValueError("drive target must retain stack boundary detection")
    netfront_stack_audit = {"send_frame_bytes": int(send[0][1]), "direct_frame_budget_bytes": 256,
                            "stack_sentinel": True,
                            "scope": "adapter direct frame; target tests still validate the caller chain"}
    spec = importlib.util.spec_from_file_location("xenova_network_build", snapshot / "platform/scripts/build-xenova-network-starter.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier.verify_event_irq(build / "zephyr/zephyr.dts")
    segments = verifier.verify_elf(build / "zephyr/zephyr.elf")
    run("package", [sys.executable, "-B", snapshot / "platform/scripts/package-xenova-firmware.py",
                    "--build-dir", build / "zephyr", "--license", args.zephyr_source / "LICENSE",
                    "--version", "0.1.0-drive-" + digest[:12], "--app", "xenova-" + args.app,
                    "--source-revision", "sha256:" + digest, "--build-id", args.output.name,
                    "--app-source", home, "--networked", "--output", args.output / "firmware", "--execute"])
    receipt = {"netfront_stack_audit": netfront_stack_audit, "app": args.app, "profile": args.profile,
               "config_sha256": sha(build / "zephyr/.config"),
               "source_files": source, "source_sha256": digest, "zephyr_commit": commit,
               "compiler_sha256": sha(compiler), "compiler_version": subprocess.check_output([str(compiler), "--version"], text=True).splitlines()[0],
               "sdk_version": lock["sdk_version"], "elf_segments": segments,
               "files": {p.name: {"sha256": sha(p), "bytes": p.stat().st_size}
                         for p in sorted((args.output / "firmware").iterdir()) if p.is_file()},
               "acceptance": {"M1_arm_build": True, "M2_board": False, "M3_performance": False}}
    (args.output / "BUILD_RECEIPT.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"candidate": str(args.output / "firmware"), "firmware_sha256": receipt["files"]["zephyr.bin"]["sha256"]}))


if __name__ == "__main__":
    main()
