#!/usr/bin/env python3
"""임시 G1 도메인만 준비·식별·제어한다. 실행은 사용자 명시 요청에서만 수행한다."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NAME = re.compile(r"xg1-[a-z0-9-]{1,32}")
MAC = re.compile(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}")
CFG = """name = "{name}"
uuid = "{uuid}"
kernel = "{kernel}"
vcpus = 1
memory = 16
gic_version = "v2"
on_crash = "preserve"
vif = ["mac={mac},bridge=xenbr0"]
"""


COMMAND_LOG_DIR = None


def run(argv, timeout=10, *, log_directory=None):
    """Preserve raw output before reporting failure; never retry a command here."""
    directory = log_directory or COMMAND_LOG_DIR
    if directory is None:
        raise ValueError("command log directory must be selected before execution")
    directory = Path(directory)
    plain(directory.parent, directory=True)
    directory.mkdir(mode=0o700, exist_ok=True)
    plain(directory, directory=True)
    record = directory/("command-"+os.urandom(16).hex())
    record.mkdir(mode=0o700)

    def save(name, content):
        fd = os.open(record/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)

    metadata = {"argv": list(argv), "timeout_seconds": timeout,
                "returncode": None, "timed_out": False}
    save("argv.json", json.dumps(metadata, ensure_ascii=False, indent=2).encode())
    started = time.monotonic()
    stdout = stderr = b""
    failure = None
    try:
        process = subprocess.run(argv, capture_output=True, timeout=timeout)
        stdout, stderr = process.stdout, process.stderr
        metadata["returncode"] = process.returncode
    except subprocess.TimeoutExpired as error:
        stdout, stderr = error.stdout or b"", error.stderr or b""
        metadata["timed_out"] = True
        failure = error
    except OSError as error:
        metadata["execution_error"] = type(error).__name__
        failure = error
    metadata["elapsed_seconds"] = time.monotonic()-started
    save("stdout", stdout)
    save("stderr", stderr)
    save("result.json", json.dumps(metadata, ensure_ascii=False, indent=2).encode())
    if failure is not None or metadata["returncode"]:
        status = "timeout" if metadata["timed_out"] else str(metadata["returncode"])
        summary = stderr.decode("utf-8", errors="replace")[:4000]
        raise RuntimeError(f"command failed ({status}); full logs: {record}\n{summary}") from failure
    return stdout.decode("utf-8", errors="replace").strip()


def plain(path, directory=False):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path or path.is_symlink():
        raise ValueError("canonical path without parent symlinks required: " + str(path))
    st = path.stat()
    if directory:
        if not stat.S_ISDIR(st.st_mode):
            raise ValueError("directory required")
    elif not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise ValueError("plain single-link file required")
    return path


def sha(path):
    return hashlib.sha256(plain(path).read_bytes()).hexdigest()


def platform(directory):
    common = ROOT / "platform/first-install"
    if common.is_dir():
        sys.path.insert(0, str(common))
    sys.path.insert(0, str(plain(directory, directory=True)))
    from xenova_vm import Platform
    return Platform(command=run)


def identities(name, identity, mac):
    if (not NAME.fullmatch(name) or not UUID.fullmatch(identity) or not MAC.fullmatch(mac)
            or identity == "00000000-0000-0000-0000-000000000000"):
        raise ValueError("xg1-* name, canonical UUID and lowercase MAC required")
    octets = bytes.fromhex(mac.replace(":", ""))
    if octets[0] & 1 or not octets[0] & 2:
        raise ValueError("locally administered unicast MAC required")


def reserved(platform):
    result = []
    root = platform.data / "instances"
    if root.exists():
        plain(root, directory=True)
        for file in root.glob("*/instance.json"):
            record = json.loads(plain(file).read_text())
            if not isinstance(record, dict) or not isinstance(record.get("uuid"), str):
                raise ValueError("invalid instance identity inventory")
            result.append((record.get("name"), record["uuid"], record.get("mac")))
    return result


def available(p, name, identity, mac):
    identities(name, identity, mac)
    p.check_xen()
    p.check_network()
    inventory = reserved(p)
    for row in p.domains():
        if row["domid"] == 0:
            continue
        config = row["config"]
        info = config["c_info"]
        inventory.append((info["name"], info["uuid"], None))
        for nic in config.get("nics", []):
            inventory.append((None, None, nic["mac"].lower()))
    for old_name, old_uuid, old_mac in inventory:
        if name == old_name or identity == old_uuid or mac == old_mac:
            raise ValueError("test name, UUID or MAC already in use/reserved")
    hosts = p.runtime / "dhcp-hosts"
    if hosts.exists():
        for line in plain(hosts).read_text().splitlines():
            if line.split(",")[0].lower() == mac:
                raise ValueError("MAC is present in DHCP reservations")


def firmware(directory):
    directory = plain(directory, directory=True)
    manifest = json.loads(plain(directory/"g1-build.json").read_text())
    if manifest.get("app") != "xenova-xenstore-g1":
        raise ValueError("G1 build manifest required")
    required = {"zephyr.bin", "zephyr.elf", "zephyr.map", "zephyr.config", "zephyr.dts",
                "ZEPHYR-LICENSE", "XEN-ABI-LICENSE"}
    if set(manifest.get("files", {})) != required:
        raise ValueError("incomplete G1 artifacts")
    for name, entry in manifest["files"].items():
        file = plain(directory/name)
        if sha(file) != entry["sha256"] or file.stat().st_size != entry["bytes"]:
            raise ValueError("G1 artifact hash/size mismatch: " + name)
    kernel = str(directory/"zephyr.bin")
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", kernel):
        raise ValueError("firmware path must not contain config delimiters or whitespace")
    return kernel


def prepare(p, args):
    kernel = firmware(args.firmware)
    available(p, args.name, args.uuid, args.mac)
    directory = args.directory
    if not directory.is_absolute() or directory.exists() or directory.is_symlink():
        raise ValueError("new absolute test directory required")
    plain(directory.parent, directory=True)
    directory.mkdir(mode=0o700)
    config = CFG.format(name=args.name, uuid=args.uuid, kernel=kernel, mac=args.mac)
    (directory/"test.cfg").write_text(config)
    lab = {"schema_version": 1, "purpose": "xenova-xenstore-g1",
           "name": args.name, "uuid": args.uuid, "mac": args.mac,
           "firmware": str(args.firmware), "config_sha256": sha(directory/"test.cfg")}
    (directory/"lab.json").write_text(json.dumps(lab, indent=2)+"\n")
    return lab


def load(directory):
    directory = plain(directory, directory=True)
    lab = json.loads(plain(directory/"lab.json").read_text())
    if lab.get("schema_version") != 1 or lab.get("purpose") != "xenova-xenstore-g1":
        raise ValueError("G1 lab identity required")
    identities(lab["name"], lab["uuid"], lab["mac"])
    expected = CFG.format(name=lab["name"], uuid=lab["uuid"],
                          kernel=firmware(Path(lab["firmware"])), mac=lab["mac"])
    if sha(directory/"test.cfg") != lab["config_sha256"] or (directory/"test.cfg").read_text() != expected:
        raise ValueError("test cfg differs from owned lab identity")
    return lab


def target(p, lab):
    if any(old_uuid == lab["uuid"] for _, old_uuid, _ in reserved(p)):
        raise ValueError("registered VM is not a disposable lab target")
    matches = [r for r in p.domains() if r["domid"] and
               (r["config"]["c_info"]["name"] == lab["name"] or
                r["config"]["c_info"]["uuid"] == lab["uuid"])]
    if len(matches) != 1:
        raise ValueError("exactly one matching test domain required")
    row = matches[0];info = row["config"]["c_info"]
    if info["name"] != lab["name"] or info["uuid"] != lab["uuid"]:
        raise ValueError("test domain identity changed")
    current = p.removal_snapshot(time.monotonic()+10).get(row["domid"])
    if not current or (current["name"],current["uuid"]) != (lab["name"],lab["uuid"]):
        raise ValueError("test domid disappeared or was reused")
    return row["domid"]


def inspect(p, lab):
    domid = target(p, lab)
    root = f"/local/domain/{domid}/device/vif"
    devices = run(["xenstore-list", root]).splitlines()
    if len(devices) != 1 or not devices[0].isdigit():
        raise ValueError("exactly one test VIF required")
    front = root+"/"+devices[0]
    backend = run(["xenstore-read",front+"/backend"])
    backend_id = run(["xenstore-read",front+"/backend-id"])
    expected = f"/local/domain/{backend_id}/backend/vif/{domid}/{devices[0]}"
    if not backend_id.isdigit() or backend != expected:
        raise ValueError("unexpected backend path")
    actual_mac = run(["xenstore-read",front+"/mac"]).lower()
    if actual_mac != lab["mac"]:
        raise ValueError("test VIF MAC changed")
    return {"domid": domid, "uuid": lab["uuid"], "frontend": front, "backend": backend,
            "backend_id": int(backend_id), "mac": actual_mac,
            "frontend_state": run(["xenstore-read",front+"/state"]),
            "backend_state": run(["xenstore-read",backend+"/state"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    default = ROOT/"platform/yocto/meta-xenova/recipes-core/xenova-platform/files"
    parser.add_argument("--platform-dir", type=Path, default=default if default.is_dir() else Path("/usr/lib/xenova"))
    parser.add_argument("--log-directory", type=Path,
                        help="raw per-command logs (default: sibling of the lab directory)")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("preflight")
    prep = sub.add_parser("prepare")
    for key in ("firmware", "directory"):
        prep.add_argument("--"+key, required=True, type=Path)
    for key in ("name", "uuid", "mac"):
        prep.add_argument("--"+key, required=True)
    for action in ("launch", "inspect", "resume", "signal", "cleanup"):
        command = sub.add_parser(action)
        command.add_argument("--directory", required=True, type=Path)
        if action != "inspect":
            command.add_argument("--execute", action="store_true", required=True)
        if action == "signal":
            command.add_argument("--path", required=True)
            command.add_argument("--value", required=True)
        if action == "cleanup":
            command.add_argument("--confirm-uuid", required=True)
            command.add_argument("--remove-config", action="store_true")
    args = parser.parse_args(argv)
    global COMMAND_LOG_DIR
    COMMAND_LOG_DIR = args.log_directory or (
        args.directory.parent/(args.directory.name+"-logs") if hasattr(args, "directory")
        else Path.cwd()/"xenova-g1-preflight-logs")
    p = platform(args.platform_dir)
    if args.action == "preflight":
        tools = ("xl","xenstore-read","xenstore-write","xenstore-list","xenstore-ls","ip","python3")
        missing = [name for name in tools if not shutil.which(name)]
        if missing:
            raise ValueError("missing tools: "+", ".join(missing))
        p.check_xen();p.check_network()
        print(json.dumps({"xl_info":run(["xl","info"]), "domains":run(["xl","list"]),
                          "store_dom0_name":run(["xenstore-read","/local/domain/0/name"]),
                          "bridge":run(["ip","-details","link","show","dev","xenbr0"])},indent=2))
        return
    if args.action == "prepare":
        print(json.dumps(prepare(p,args),indent=2));return
    lab = load(args.directory)
    if args.action == "launch":
        available(p,lab["name"],lab["uuid"],lab["mac"])
        print(run(["xl","create","-p",str(args.directory/"test.cfg")],timeout=60))
        print(json.dumps({"domid":target(p,lab),"paused":True},indent=2))
    elif args.action == "inspect":
        print(json.dumps(inspect(p,lab),indent=2))
    elif args.action == "resume":
        domid = target(p,lab);print(run(["xl","unpause",str(domid)]))
    elif args.action == "signal":
        domid = target(p,lab)
        if not re.fullmatch(rf"/local/domain/{domid}/data/xenova-g1-[0-9a-f]+/control",args.path):
            raise ValueError("only this guest's dedicated G1 control node may be changed")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",args.value):
            raise ValueError("simple test marker required")
        print("before="+run(["xenstore-read",args.path]))
        target(p,lab)
        run(["xenstore-write",args.path,args.value])
        # The guest may remove this node immediately after consuming the notification.
        print("written="+args.value+"; verify guest watch-reread and exercise-result")
    elif args.action == "cleanup":
        if args.confirm_uuid != lab["uuid"]:
            raise ValueError("explicit UUID confirmation mismatch")
        domid = target(p,lab)
        run(["xl","destroy",str(domid)])
        end = time.monotonic()+10
        expected = {(domid,lab["uuid"],lab["name"])}
        while True:
            snapshots = p.removal_observation(expected,end)
            if all(domid not in snapshot for snapshot in snapshots):
                break
            if time.monotonic() >= end:
                raise RuntimeError("test domain removal not confirmed; assets retained")
            time.sleep(0.1)
        if args.remove_config:
            if {q.name for q in args.directory.iterdir()} != {"lab.json","test.cfg"}:
                raise ValueError("extra assets found; preserve directory")
            for name in ("test.cfg","lab.json"):
                plain(args.directory/name)
            for name in ("test.cfg","lab.json"):
                (args.directory/name).unlink()
            args.directory.rmdir()
        print("test domain removed; input firmware preserved")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        raise SystemExit(str(error))
