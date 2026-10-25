#!/usr/bin/env python3
"""고정 Zephyr/SDK로 별도 network Starter 시험 펌웨어를 빌드한다. 기존 템플릿은 게시·수정하지 않는다."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "platform/guests/zephyr"
sys.path.insert(0, str(ROOT / "platform/first-install"))
from artifact_io import new_path, sha


def inputs():
    paths=[p for directory in ("xen-store","xen-runtime","diagnostics","telemetry","network-starter-app")
           for p in sorted((BASE/directory).rglob("*")) if p.is_file()]
    paths += [BASE/"network-xen.cfg.in",BASE/"network-starter.lock.json"]
    return {str(p.relative_to(BASE)):{"sha256":sha(p),"bytes":p.stat().st_size} for p in paths}


def candidate_lock(locked, files, version=None):
    """Keep factory selection fixed; candidate provenance is local to this build."""
    if version is None:
        if locked['app_files'] != files:
            raise ValueError('network Starter app source lock mismatch; explicit candidate version required')
        return dict(locked)
    if (not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?', version)
            or version == locked['version']):
        raise ValueError('candidate requires a distinct explicit application SemVer')
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {**locked, 'id': 'xenova-zephyr-network-starter-' + version, 'version': version,
            'app_files': files, 'app_source_sha256': digest}



def verify_event_irq(path):
    """생성된 Zephyr DTS의 network Starter event PPI31·level·priority를 확인한다."""
    text = re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(), flags=re.S)
    nodes = re.findall(r"\bhypervisor@[0-9a-fA-F]+\s*\{([^{}]*)\}", text, re.S)
    if len(nodes) != 1 or not re.search(r'compatible\s*=\s*"xen,xen"\s*;', nodes[0]):
        raise ValueError("network Starter DTS requires one Xen hypervisor node")
    properties = re.findall(r"\binterrupts\s*=\s*<([^<>]+)>\s*;", nodes[0])
    try:
        cells = [int(cell, 0) for cell in properties[0].split()] if len(properties) == 1 else []
    except ValueError:
        cells = []
    # Zephyr IRQ_TYPE_LEVEL=2. Standard DT level-low=8과 다른 encoding이다.
    if cells != [1, 15, 2, 160]:
        raise ValueError("network Starter event PPI31 must use Zephyr level trigger and frozen priority")


def run(command, cwd, env, output, name):
    with (output / (name + ".log")).open("w") as log:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    (output / (name + ".command.json")).write_text(json.dumps(
        {"argv": list(map(str, command)), "exit_code": result.returncode}, indent=2) + "\n")
    if result.returncode:
        raise RuntimeError(f"{name} failed ({result.returncode}); see {output / (name + '.log')}")


def verify_elf(path):
    data = path.read_bytes()
    if data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
        raise ValueError("expected little-endian AArch64 ELF")
    offset = struct.unpack_from("<Q", data, 32)[0]
    size, count = struct.unpack_from("<HH", data, 54)
    loads = []
    for i in range(count):
        kind, flags, off, virt, phys, filesz, memsz, align = struct.unpack_from(
            "<IIQQQQQQ", data, offset + i*size)
        if kind != 1:
            continue
        if not 0x40000000 <= phys < 0x41000000 or phys + memsz > 0x41000000:
            raise ValueError("ELF load segment exceeds frozen 16MiB RAM")
        loads.append({"address": phys, "file_bytes": filesz, "memory_bytes": memsz})
    if not loads:
        raise ValueError("no ELF load segment")
    return loads


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("zephyr-source", "tools", "spdx", "toolchain-archive", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--candidate-version", help="new standalone candidate version; never changes source/factory selection locks")
    parser.add_argument("--diagnostics", choices=("on", "off"), default="on")
    parser.add_argument("--telemetry", choices=("on", "off"),
                        help="v2 Publisher; defaults to on with diagnostics, off without it")
    parser.add_argument("--timing-exercise", action="store_true",
                        help="enable the optional periodic arithmetic exercise; no driving output")
    args = parser.parse_args(argv)
    args.telemetry = args.telemetry or ("on" if args.diagnostics == "on" else "off")
    if args.telemetry == "on" and args.diagnostics == "off":
        raise ValueError("Telemetry requires Diagnostics Core")
    for path in (args.zephyr_source, args.tools, args.spdx, args.toolchain_archive):
        if not path.is_absolute() or path.resolve() != path or path.is_symlink():
            raise ValueError("canonical absolute input paths required")
    new_path(args.output)
    for path in (ROOT, args.zephyr_source, args.tools):
        if args.output.is_relative_to(path):
            raise ValueError("output must be outside repository/source/tools")
    spec = importlib.util.spec_from_file_location("network-starter_source_verify", ROOT/"platform/scripts/prepare-xenova-zephyr.py")
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)
    count = verify.verify_source(args.zephyr_source, args.spdx, args.toolchain_archive)
    reference = json.loads((BASE/"xen-store/references.lock.json").read_text())
    for name, expected in reference["zephyr"]["api_files"].items():
        if sha(args.zephyr_source/name) != expected:
            raise ValueError("frozen Zephyr API mismatch: " + name)
    for name, entry in reference["xen"]["abi_files"].items():
        if sha(BASE/"xen-store/include/xenova/abi"/name) != entry["sha256"]:
            raise ValueError("frozen Xen ABI mismatch: " + name)
    for name, expected in json.loads((BASE/'xen-runtime/references/lock.json').read_text()).items():
        if sha(BASE/'xen-runtime/references'/name) != expected['sha256']:
            raise ValueError('frozen network ABI mismatch: '+name)
    app_lock=json.loads((BASE/'network-starter.lock.json').read_text())
    app_files={str(p.relative_to(BASE/'network-starter-app')):{'sha256':sha(p),'bytes':p.stat().st_size}
               for p in sorted((BASE/'network-starter-app').rglob('*')) if p.is_file()}
    app_lock = candidate_lock(app_lock, app_files, args.candidate_version)
    source_inputs = inputs()
    source_hash = hashlib.sha256(json.dumps(source_inputs, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()
    cmake = args.tools/"python/bin/cmake"
    gcc = args.tools/"aarch64-zephyr-elf/bin/aarch64-zephyr-elf-gcc"
    if not cmake.is_file() or not gcc.is_file():
        raise ValueError("existing CMake and frozen AArch64 SDK required")
    print(json.dumps({"action": "build-network-starter" if args.execute else "plan", "output": str(args.output),
                      "source_sha256": source_hash, "zephyr_source_files_verified": count,
                      "diagnostics": args.diagnostics, "telemetry": args.telemetry,
                      "timing_exercise": args.timing_exercise,
                      "candidate_id": app_lock['id']}, indent=2))
    if not args.execute:
        return
    args.output.mkdir(mode=0o755)
    snapshot = args.output/"input"
    snapshot.mkdir()
    for directory in ("xen-store", "xen-runtime", "diagnostics", "telemetry", "network-starter-app"):
        shutil.copytree(BASE/directory, snapshot/directory)
    for name in ("network-xen.cfg.in","network-starter.lock.json"):
        shutil.copyfile(BASE/name,snapshot/name)
    for name, info in source_inputs.items():
        path = snapshot/name
        if sha(path) != info["sha256"]:
            raise ValueError("source changed during snapshot")
        path.chmod(0o444)
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=str(args.tools/"python"),
               ZEPHYR_BASE=str(args.zephyr_source), ZEPHYR_TOOLCHAIN_VARIANT="cross-compile",
               CROSS_COMPILE=str(args.tools/"aarch64-zephyr-elf/bin/aarch64-zephyr-elf-"),
               XDG_CACHE_HOME=str(args.output/"cache"))
    env["PATH"] = str(args.tools/"python/bin")+os.pathsep+env["PATH"]
    build = args.output/"build"
    variant = args.output/"variant.conf"
    variant.write_text("CONFIG_XENOVA_DIAGNOSTICS=" + ("y" if args.diagnostics == "on" else "n") +
                       "\nCONFIG_XENOVA_TELEMETRY=" + ("y" if args.telemetry == "on" else "n") +
                       "\nCONFIG_XENOVA_DIAGNOSTICS_EXERCISE=" + ("y" if args.timing_exercise else "n") + "\n")
    run([str(cmake), "-S", str(snapshot/"network-starter-app"), "-B", str(build), "-GNinja",
         "-DBOARD=xenvm", "-DUSER_CACHE_DIR="+str(args.output/"cache"),
         "-DEXTRA_CONF_FILE="+str(variant),
         "-DPython3_EXECUTABLE="+sys.executable,
         "-DWEST="+str(args.tools/"python/bin/west")], ROOT, env, args.output, "configure")
    run([str(cmake), "--build", str(build), "-j", "4"], ROOT, env, args.output, "compile")
    artifacts = build/"zephyr"
    config = (artifacts/".config").read_text().splitlines()
    required = ("CONFIG_ARM64=y", "CONFIG_XEN=y", "CONFIG_XEN_EVENTS=y", "CONFIG_MMU=y",
                "CONFIG_XENOVA_XENSTORE=y", "CONFIG_XENOVA_NETFRONT=y", "CONFIG_NET_DHCPV4=y", "CONFIG_NET_ARP=y", "CONFIG_GIC_V2=y", "CONFIG_UART_XEN_HVC=y",
                "CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME=y",
                "CONFIG_SHELL_BACKEND_SERIAL_API_INTERRUPT_DRIVEN=y",
                "CONFIG_SRAM_BASE_ADDRESS=0x40000000", "CONFIG_SRAM_SIZE=16384")
    if any(line not in config for line in required) or "CONFIG_NET_L2_ETHERNET=y" not in config:
        raise ValueError("network Starter build differs from frozen xenvm/Store profile")
    if ("CONFIG_XENOVA_DIAGNOSTICS=y" in config) != (args.diagnostics == "on"):
        raise ValueError("diagnostics variant mismatch")
    if ("CONFIG_XENOVA_TELEMETRY=y" in config) != (args.telemetry == "on"):
        raise ValueError("Telemetry variant mismatch")
    if args.telemetry == "on" and any(line not in config for line in
            ("CONFIG_NET_TCP=y", "CONFIG_MQTT_LIB=y", "CONFIG_MQTT_LIB_CUSTOM_TRANSPORT=y")):
        raise ValueError("Telemetry requires the real TCP/MQTT transport")
    if ("CONFIG_XENOVA_DIAGNOSTICS_EXERCISE=y" in config) != args.timing_exercise:
        raise ValueError("periodic exercise variant mismatch")
    if args.diagnostics == "on" and "CONFIG_XENOVA_DIAGNOSTICS_TIMING=y" not in config:
        raise ValueError("xenvm diagnostics requires the actual 64-bit cycle counter")
    verify_event_irq(artifacts/"zephyr.dts")
    binary = (artifacts/"zephyr.bin").read_bytes()
    if binary[56:60] != b"ARM\x64":
        raise ValueError("expected ARM64 Image header")
    loads = verify_elf(artifacts/"zephyr.elf")
    payload = args.output/"firmware"
    payload.mkdir()
    for name, source in {"zephyr.bin": artifacts/"zephyr.bin", "zephyr.elf": artifacts/"zephyr.elf",
                         "zephyr.map": artifacts/"zephyr.map", "zephyr.config": artifacts/".config",
                         "zephyr.dts": artifacts/"zephyr.dts",
                         "ZEPHYR-LICENSE": args.zephyr_source/"LICENSE",
                         "XEN-ABI-LICENSE": BASE/"xen-store/LICENSES/MIT-Xen"}.items():
        shutil.copyfile(source, payload/name)
        (payload/name).chmod(0o444)
    report = {
        "schema_version": 1, "app": "xenova-xenstore-network-starter",
        "repository_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": source_hash, "source_files": source_inputs,
        "app_lock": app_lock,
        "build_script_sha256": sha(Path(__file__)), "references": reference,
        "diagnostics": args.diagnostics, "telemetry": args.telemetry,
        "timing_exercise": args.timing_exercise,
        "variant_config_sha256": sha(variant),
        "toolchain_archive_sha256": sha(args.toolchain_archive),
        "compiler_sha256": sha(gcc), "elf_load_segments": loads,
        "files": {p.name: {"sha256": sha(p), "bytes": p.stat().st_size}
                  for p in sorted(payload.iterdir())}}
    if inputs() != source_inputs:
        raise ValueError("repository network Starter inputs changed during build; output retained for investigation")
    (payload/"network-starter-build.json").write_text(json.dumps(report, indent=2)+"\n")
    (payload/"network-starter-build.json").chmod(0o444)
    print(json.dumps({"firmware": str(payload), "files": report["files"]}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
