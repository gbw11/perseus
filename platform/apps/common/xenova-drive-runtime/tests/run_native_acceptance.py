#!/usr/bin/env python3
"""Run the REAL autonomous/manual binaries in a new isolated network namespace.

Two authenticated listeners separate v1 driving and v2 telemetry permissions.
Only synthetic input is generated here; commands must come from the Zephyr apps.
This is M1/M2 evidence, never RPi4 performance or CPU-pinning acceptance.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import uuid

BASE = Path(__file__).resolve().parents[1]


def private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(value if isinstance(value, str) else json.dumps(value))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manual", type=Path, required=True)
    p.add_argument("--autonomous", type=Path, required=True)
    p.add_argument("--tools-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--duration", type=int, default=18)
    p.add_argument("--extended", action="store_true")
    args = p.parse_args()
    if os.geteuid() or os.readlink("/proc/self/ns/net") == os.readlink("/proc/1/ns/net"):
        p.error("root in a fresh network namespace required")
    if not 12 <= args.duration <= 60:
        p.error("native functional run duration must be 12..60 seconds")
    args.output.mkdir(mode=0o700)
    secret = args.output.with_name(args.output.name + "-private")
    secret.mkdir(mode=0o700)
    tool = args.tools_root / "root"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               LD_LIBRARY_PATH=str(tool / "usr/lib/x86_64-linux-gnu"))
    children, handles, commands = [], [], []

    def run(argv):
        result = subprocess.run(list(map(str, argv)), env=env, capture_output=True, timeout=20)
        commands.append({"argv": list(map(str, argv)), "rc": result.returncode})
        if result.returncode:
            raise RuntimeError("isolated setup failed: " + str(argv[0]))

    def spawn(label, argv, child_env=None):
        stdout = (args.output / (label + ".stdout")).open("wb")
        stderr = (args.output / (label + ".stderr")).open("wb")
        handles.extend([stdout, stderr])
        child = subprocess.Popen(list(map(str, argv)), env=child_env or env, stdout=stdout, stderr=stderr)
        children.append(child)
        commands.append({"label": label, "argv": list(map(str, argv)), "pid": child.pid})
        return child

    def wait(child, timeout):
        rc = child.wait(timeout=timeout)
        next(c for c in commands if c.get("pid") == child.pid)["rc"] = rc
        if rc:
            raise RuntimeError("owned test child failed: " + str(child.pid))

    try:
        for argv in (["ip", "link", "set", "lo", "up"],
                     ["ip", "link", "add", "xdbr", "type", "bridge"],
                     ["ip", "address", "add", "192.0.2.1/24", "dev", "xdbr"],
                     ["ip", "link", "set", "xdbr", "up"]):
            run(argv)
        guests = []
        bindings = []
        for index, app in enumerate(("manual", "autonomous")):
            tap = "xdtap" + str(index)
            for argv in (["ip", "tuntap", "add", "dev", tap, "mode", "tap"],
                         ["ip", "link", "set", tap, "master", "xdbr"], ["ip", "link", "set", tap, "up"]):
                run(argv)
            vm = str(uuid.uuid4())
            bootstrap = {"vm_id": vm, "boot_id": str(uuid.uuid4()), "publisher_id": str(uuid.uuid4()),
                         "host_boot_id": "", "execution_id": "", "receipt_id": "", "broker_ipv4": "192.0.2.1",
                         "username": vm, "trusted_internal_plaintext": True}
            for channel, port in (("drive", 18884), ("telemetry", 18883)):
                value = {**bootstrap, "publisher_id": str(uuid.uuid4()), "port": port, "password": secrets.token_urlsafe(32)}
                private(secret / (app + "-" + channel + ".json"), value)
                if channel == "telemetry":
                    bootstrap["publisher_id"] = value["publisher_id"]
            bindings.append({"vm_id": vm, "boot_id": bootstrap["boot_id"],
                             "publisher_instance_id": bootstrap["publisher_id"]})
            guests.append((app, tap, index, vm))
        (args.output / "bindings.json").write_text(json.dumps(bindings, indent=2))
        for user in ("subscriber", "input"):
            private(secret / (user + ".json"), {"username": user, "password": secrets.token_urlsafe(32)})
        for channel, port in (("drive", 18884), ("telemetry", 18883)):
            subscriber = json.loads((secret / "subscriber.json").read_text())
            passwords = "subscriber:" + subscriber["password"] + "\n"
            acl = "user subscriber\ntopic read xenova/#\n"
            if channel == "drive":
                sender = json.loads((secret / "input.json").read_text())
                passwords += "input:" + sender["password"] + "\n"
                acl += "user input\ntopic write xenova/v1/vehicle/control\ntopic write xenova/v1/apps/manual/input\ntopic write xenova/v1/apps/perception/events\n"
            for app, _, _, vm in guests:
                credential = json.loads((secret / (app + "-" + channel + ".json")).read_text())
                passwords += vm + ":" + credential["password"] + "\n"
                acl += "user " + vm + "\n"
                if channel == "drive":
                    acl += f"topic write xenova/v1/apps/{app}/events\ntopic read xenova/v1/vehicle/control\n"
                    acl += "topic read " + ("xenova/v1/apps/manual/input" if app == "manual" else "xenova/v1/apps/perception/events") + "\n"
                else:
                    acl += f"topic write xenova/v2/vms/{vm}/telemetry/+\n"
            password_file = secret / (channel + "-passwords")
            private(password_file, passwords)
            run([tool / "usr/bin/mosquitto_passwd", "-U", password_file])
            private(secret / (channel + "-acl"), acl)
            private(secret / (channel + ".conf"), f"listener {port} 192.0.2.1\nuser root\nallow_anonymous false\npassword_file {password_file}\nacl_file {secret / (channel + '-acl')}\npersistence false\nlog_type error\n")
        brokers = {channel: spawn("broker-" + channel, [tool / "usr/sbin/mosquitto", "-c", secret / (channel + ".conf")])
                   for channel in ("drive", "telemetry")}
        time.sleep(0.3)
        if any(child.poll() is not None for child in brokers.values()):
            raise RuntimeError("isolated listener did not start")
        probe = BASE / "tools/drive_probe.py"
        readers = []
        for channel, port in (("drive", 18884), ("telemetry", 18883)):
            readers.append(spawn("receive-" + channel, [sys.executable, "-B", probe, "receive", "--host", "192.0.2.1",
                                "--port", port, "--credentials", secret / "subscriber.json", "--bindings", args.output / "bindings.json",
                                "--output", args.output / (channel + ".jsonl"), "--duration", args.duration + 2, "--isolated-test-network"]))
        time.sleep(0.5)
        binaries = []
        for app, tap, index, _ in guests:
            guest_env = dict(env, XT_TEST_BOOTSTRAP=str(secret / (app + "-telemetry.json")),
                             XD_TEST_APP_BOOTSTRAP=str(secret / (app + "-drive.json")),
                             XT_TEST_INSTANCE_ID=guests[index][3], XD_TEST_DURATION_MS=str(args.duration * 1000))
            binaries.append(spawn(app, [getattr(args, app), "-rt", "-no-color", "-eth-if=" + tap,
                                       f"-mac-addr=02:00:5e:00:53:0{index + 2}", f"-ipv4-addr=192.0.2.{index + 2}",
                                       "-ipv4-gw=192.0.2.1", "-ipv4-nm=255.255.255.0"], guest_env))
        sender = spawn("input", [sys.executable, "-B", probe, "input", "--host", "192.0.2.1", "--port", 18884,
                                  "--credentials", secret / "input.json", "--output", args.output / "inputs.jsonl",
                                  "--duration", args.duration - 1, "--isolated-test-network"])
        if args.extended:
            time.sleep(6)
            brokers["drive"].terminate()
            wait(brokers["drive"], 5)
            time.sleep(2)
            brokers["drive"] = spawn("broker-drive-reconnect", [tool / "usr/sbin/mosquitto", "-c", secret / "drive.conf"])
        for child in binaries + [sender] + readers:
            wait(child, args.duration + 15)
        summaries = {channel: json.loads((args.output / (channel + ".summary.json")).read_text())
                     for channel in ("drive", "telemetry")}
        if any(s["invalid"] or s["queue_dropped"] or s["writer_errors"] for s in summaries.values()):
            raise RuntimeError("receiver validation/drop failure")
        analyze = [sys.executable, "-B", BASE / "tools/analyze.py", args.output,
                   "--output", args.output / "ANALYSIS.json"]
        if not args.extended:
            analyze.append("--require-lossless")
        wait(spawn("analyze", analyze), 15)
        print(json.dumps({"stage": "native-functional-only", "receiver": summaries}))
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        for handle in handles:
            handle.close()
        for child in children:
            next(c for c in commands if c.get("pid") == child.pid)["rc"] = child.returncode
        (args.output / "commands.json").write_text(json.dumps(commands, indent=2))


if __name__ == "__main__":
    main()
