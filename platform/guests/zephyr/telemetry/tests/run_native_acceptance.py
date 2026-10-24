#!/usr/bin/env python3
"""Actual Zephyr Publisher -> private Mosquitto -> independent subscriber.

Run ONLY inside a new network namespace, as root. All credentials are ephemeral
private files outside the repository and are excluded from result artifacts.
No production service, network interface or board is accessed.
"""
from __future__ import annotations
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import struct
import subprocess
import sys
import time
import uuid

BASE = Path(__file__).resolve().parents[1]


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(value if isinstance(value, str) else json.dumps(value))


def public_binding(bootstrap):
    return {"vm_id": bootstrap["vm_id"], "boot_id": bootstrap["boot_id"],
            "publisher_instance_id": bootstrap["publisher_id"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--tools-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", required=True)
    parser.add_argument("--extended", action="store_true", help="ACL, disconnect, native boot/session replacement")
    args = parser.parse_args()
    if os.geteuid() or os.readlink("/proc/self/ns/net") == os.readlink("/proc/1/ns/net"):
        parser.error("root in a new isolated network namespace required")
    args.output.mkdir(mode=0o700)
    private = args.output.parent / (args.output.name + "-private")
    private.mkdir(mode=0o700)
    tools = args.tools_root / "root"
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               LD_LIBRARY_PATH=str(tools / "usr/lib/x86_64-linux-gnu"))
    commands = []
    children = []
    handles = []

    def run(argv, **kwargs):
        started = time.monotonic()
        result = subprocess.run(list(map(str, argv)), env=env, capture_output=True, timeout=15, **kwargs)
        commands.append({"argv": list(map(str, argv)), "rc": result.returncode,
                         "elapsed_s": time.monotonic() - started})
        if result.returncode:
            raise RuntimeError("local setup failed: " + str(argv[0]))
        return result

    def spawn(label, argv, child_env=None):
        out = (args.output / (label + ".stdout")).open("wb")
        err = (args.output / (label + ".stderr")).open("wb")
        handles.extend([out, err])
        process = subprocess.Popen(list(map(str, argv)), env=child_env or env, stdout=out, stderr=err)
        children.append(process)
        commands.append({"label": label, "argv": list(map(str, argv)), "pid": process.pid})
        return process

    def wait(process, timeout=10):
        rc = process.wait(timeout=timeout)
        for command in commands:
            if command.get("pid") == process.pid:
                command["rc"] = rc
        if rc:
            raise RuntimeError("owned test process failed, pid=" + str(process.pid))

    def subscriber(label, duration):
        return spawn(label, [sys.executable, "-B", BASE / "tools/reference_subscriber.py",
                     "--host", "192.0.2.1", "--port", "18883", "--credentials", private / "subscriber.json",
                     "--bindings-file", bindings_path, "--output", args.output / label,
                     "--duration", str(duration), "--internal-plaintext"])

    try:
        run(["ip", "link", "set", "lo", "up"])
        run(["ip", "link", "add", "xtbr", "type", "bridge"])
        run(["ip", "address", "add", "192.0.2.1/24", "dev", "xtbr"])
        run(["ip", "link", "set", "xtbr", "up"])
        for tap in ("xtapA", "xtapB"):
            run(["ip", "tuntap", "add", "dev", tap, "mode", "tap"])
            run(["ip", "link", "set", tap, "master", "xtbr"])
            run(["ip", "link", "set", tap, "up"])
        bootstraps = []
        for _ in range(2):
            vm_id = str(uuid.uuid4())
            bootstraps.append({"vm_id": vm_id, "boot_id": str(uuid.uuid4()), "publisher_id": str(uuid.uuid4()),
                               "host_boot_id": "", "execution_id": "", "receipt_id": "",
                               "broker_ipv4": "192.0.2.1", "port": 18883, "username": vm_id,
                               "password": secrets.token_urlsafe(32), "trusted_internal_plaintext": True})
        subscriber_password = secrets.token_urlsafe(32)
        audit_password = secrets.token_urlsafe(32)
        write_private(private / "subscriber.json", {"username": "telemetry-reference-subscriber", "password": subscriber_password})
        password_path = private / "passwords"
        write_private(password_path, "telemetry-reference-subscriber:" + subscriber_password + "\ntelemetry-test-audit:" + audit_password + "\n" +
                      "".join(b["username"] + ":" + b["password"] + "\n" for b in bootstraps))
        run([tools / "usr/bin/mosquitto_passwd", "-U", password_path])
        acl = "user telemetry-reference-subscriber\ntopic read xenova/v2/vms/+/telemetry/+\nuser telemetry-test-audit\ntopic read xenova/#\n"
        for b in bootstraps:
            acl += "user " + b["username"] + "\n"
            for stream in ("inventory", "heartbeat", "metrics", "events", "snapshot"):
                acl += f"topic write xenova/v2/vms/{b['vm_id']}/telemetry/{stream}\n"
        write_private(private / "acl", acl)
        write_private(private / "mosquitto.conf", f"listener 18883 192.0.2.1\nuser root\nallow_anonymous false\npassword_file {password_path}\nacl_file {private / 'acl'}\npersistence false\nlog_type error\n")
        broker = spawn("broker", [tools / "usr/sbin/mosquitto", "-c", private / "mosquitto.conf"])
        time.sleep(0.3)
        if broker.poll() is not None:
            raise RuntimeError("private broker did not start")
        bindings_path = args.output / "bindings.json"
        bindings_path.write_text(json.dumps([public_binding(b) for b in bootstraps], indent=2))
        sub = subscriber("initial", 10 if args.extended else 14)
        time.sleep(0.3)
        guests = []
        source_runs = []
        started_guests = time.monotonic()
        for index, bootstrap in enumerate(bootstraps):
            label = "AB"[index]
            path = private / (label + ".json")
            write_private(path, bootstrap)
            guest_env = dict(env, XT_TEST_BOOTSTRAP=str(path), XT_TEST_INSTANCE_ID=bootstrap["vm_id"],
                             XT_TEST_DURATION_MS="32000" if args.extended and index == 0 else "12000", XT_TEST_STALL_MS="6000")
            if args.extended and index == 0:
                guest_env.update(XT_TEST_RESTART_MS="25000", XT_TEST_ERROR_BURST="1")
            guests.append(spawn("guest-" + label, [args.binary, "-rt", "-no-color", "-eth-if=xtap" + label,
                                  f"-mac-addr=02:00:5e:00:53:0{index + 2}", f"-ipv4-addr=192.0.2.{index + 2}",
                                  "-ipv4-gw=192.0.2.1", "-ipv4-nm=255.255.255.0"], guest_env))
            source_runs.append(("guest-" + label, bootstrap["vm_id"], bootstrap["boot_id"]))
        fault_result = {}
        if args.extended:
            spec = importlib.util.spec_from_file_location("readonly_peer", BASE / "tools/reference_subscriber.py")
            peer = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(peer)
            audit = peer.ReadOnlySubscriber("192.0.2.1", 18883,
                     {"username": "telemetry-test-audit", "password": audit_password}, "xenova/#")
            def raw_connect(username, password, anonymous=False):
                sock = socket.create_connection(("192.0.2.1", 18883), timeout=2)
                sock.settimeout(2)
                body = peer.utf8("MQTT") + bytes([4, 2 if anonymous else 0xc2]) + struct.pack("!H", 30)
                body += peer.utf8("xt2-test-" + uuid.uuid4().hex)
                if not anonymous:
                    body += peer.utf8(username) + peer.utf8(password)
                sock.sendall(peer.packet(0x10, body))
                reader = peer.ReadOnlySubscriber.__new__(peer.ReadOnlySubscriber)
                reader.sock = sock
                kind, reply = reader.receive()
                return sock, reader, kind == 0x20 and reply == b"\0\0"
            bad, _, accepted = raw_connect(bootstraps[0]["username"], secrets.token_urlsafe(20))
            bad.close()
            assert not accepted
            anon, _, accepted = raw_connect("", "", True)
            anon.close()
            assert not accepted
            probe = ("xt2-forbidden-probe-" + uuid.uuid4().hex).encode()
            acked_denials = 0
            for topic in (f"xenova/v2/vms/{bootstraps[1]['vm_id']}/telemetry/metrics", "xenova/v1/vehicles/test/drive/command"):
                sock, reader, accepted = raw_connect(bootstraps[0]["username"], bootstraps[0]["password"])
                assert accepted
                sock.sendall(peer.packet(0x32, peer.utf8(topic) + b"\0\7" + probe))
                try:
                    kind, reply = reader.receive()
                    acked_denials += kind == 0x40
                except (EOFError, socket.timeout):
                    pass
                sock.close()
            audit.sock.settimeout(0.2)
            until = time.monotonic() + 1
            while time.monotonic() < until:
                try:
                    kind, data = audit.receive()
                    assert probe not in data, "forbidden publish reached independent audit subscriber"
                    if kind >> 4 == 3 and ((kind >> 1) & 3) == 1:
                        size = int.from_bytes(data[:2], "big")
                        audit.sock.sendall(peer.packet(0x40, data[2 + size:4 + size]))
                except socket.timeout:
                    continue
            audit.close()
            wait(sub, 15)
            broker.terminate()
            broker.wait(timeout=4)
            wait(guests[1], 7)  # B's Publisher must stop while the broker is absent.
            time.sleep(3)
            broker = spawn("broker-restarted", [tools / "usr/sbin/mosquitto", "-c", private / "mosquitto.conf"])
            time.sleep(0.3)
            sub = subscriber("reconnected", 20)
            # Native process replacement models a fresh guest boot; it is not a Xen reboot test.
            replacement = dict(bootstraps[1], boot_id=str(uuid.uuid4()), publisher_id=str(uuid.uuid4()))
            write_private(private / "B-reboot.json", replacement)
            b_env = dict(env, XT_TEST_BOOTSTRAP=str(private / "B-reboot.json"), XT_TEST_INSTANCE_ID=replacement["vm_id"],
                         XT_TEST_DURATION_MS="14000", XT_TEST_STALL_MS="6000")
            guests.append(spawn("guest-B-reboot", [args.binary, "-rt", "-no-color", "-eth-if=xtapB",
                                  "-mac-addr=02:00:5e:00:53:03", "-ipv4-addr=192.0.2.3",
                                  "-ipv4-gw=192.0.2.1", "-ipv4-nm=255.255.255.0"], b_env))
            source_runs.append(("guest-B-reboot", replacement["vm_id"], replacement["boot_id"]))
            while time.monotonic() - started_guests < 23:
                time.sleep(0.1)
            restarted_a = dict(bootstraps[0], publisher_id=str(uuid.uuid4()))
            # Atomic private bootstrap update; no credentials in argv or result.
            write_private(private / "A-next.json", restarted_a)
            os.replace(private / "A-next.json", private / "A.json")
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                text = (args.output / "guest-A.stdout").read_text()
                if restarted_a["publisher_id"] in text:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("actual Publisher restart/session receipt missing")
            bindings_path.write_text(json.dumps([public_binding(restarted_a), public_binding(replacement)]))
            for guest in (guests[0], guests[2]):
                wait(guest, 15)
            wait(sub, 10)
            fault_result = {"bad_credentials_rejected": True, "anonymous_rejected": True,
                            "other_vm_and_command_delivery_denied": True, "pubacks_for_denied_probes": acked_denials,
                            "broker_disconnect_reconnect": True, "stop_without_broker": True,
                            "publisher_restart_same_boot": True, "native_process_new_boot": True}
        else:
            for guest in guests:
                wait(guest, 20)
            wait(sub, 10)
        late = subscriber("retained", 3)
        wait(late, 8)
        logs = [args.output / "initial/received.jsonl"]
        if args.extended:
            logs.append(args.output / "reconnected/received.jsonl")
        records = [json.loads(line) for path in logs for line in path.read_text().splitlines()]
        assert records and all(r["valid"] for r in records)
        messages = [json.loads(base64.b64decode(r["payload_base64"])) for r in records]
        assert {m["vm_id"] for m in messages} == {b["vm_id"] for b in bootstraps}
        assert {m["type"] for m in messages} == {"telemetry." + s for s in ("inventory", "heartbeat", "metrics", "events", "snapshot")}
        matched = 0
        sources = {}
        for label, vm_id, boot_id in source_runs:
            for line in (args.output / (label + ".stdout")).read_text().splitlines():
                if line.startswith("XT_SOURCE "):
                    source = json.loads(line[len("XT_SOURCE "):])
                    sources.setdefault((vm_id, boot_id, source["start_ms"], source["end_ms"]), []).append(source)
        for index, bootstrap in enumerate(bootstraps):
            loops = []
            for m in messages:
                if m["vm_id"] != bootstrap["vm_id"] or m["type"] != "telemetry.metrics":
                    continue
                p = m["payload"]
                candidates = sources[(m["vm_id"], m["boot_id"], p["collection"]["start_uptime_ms"], p["collection"]["end_uptime_ms"])]
                values = {s["name"]: s["value"] for s in p.get("samples", [])}
                if p["family"] == "memory":
                    assert any(values["memory.kernel_heap.used_bytes"] == s["heap_used"] and
                               values["memory.kernel_heap.free_bytes"] == s["heap_free"] for s in candidates)
                    matched += 1
                if p["family"] == "loop_progress":
                    assert any(values["loop.completed_total"] == s["completed"] and
                               values["loop.in_flight"] == s["in_flight"] for s in candidates)
                    if values["loop.last_jitter_us"] is not None:
                        assert any(values["loop.last_jitter_us"] == s["jitter"] for s in candidates)
                    loops.append((m["boot_id"], int(p["collection"]["end_uptime_ms"]), values))
                    matched += 1
            for boot in {boot for boot, _, _ in loops}:
                stalled = [values for b, end, values in loops if b == boot and end > 7500]
                assert len(stalled) >= 2
                assert len({s["loop.completed_total"] for s in stalled}) == 1
                assert all(s["loop.in_flight"] for s in stalled)
                assert int(stalled[-1]["loop.last_completed_age_us"]) > int(stalled[0]["loop.last_completed_age_us"])
        if args.extended:
            a_messages = [m for m in messages if m["vm_id"] == bootstraps[0]["vm_id"]]
            assert len({m["publisher_instance_id"] for m in a_messages}) == 2
            assert len({m["boot_id"] for m in a_messages}) == 1
            epochs = {s["labels"]["task_epoch"] for m in a_messages if m["type"] == "telemetry.metrics"
                      for s in m["payload"].get("samples", []) if s["name"] == "loop.completed_total"}
            assert len(epochs) == 1, "Publisher restart must preserve the continuing Core task epoch"
            b_messages = [m for m in messages if m["vm_id"] == bootstraps[1]["vm_id"]]
            assert len({m["boot_id"] for m in b_messages}) == 2
            assert any(m["type"] == "telemetry.events" and int(m["payload"]["coverage"]["publisher_dropped_total"]) > 0 for m in a_messages)
            assert all(int(m["clock"]["sent_uptime_ms"]) - int(m["payload"]["collection"]["end_uptime_ms"]) <= 2000 for m in messages if m["type"] in ("telemetry.metrics", "telemetry.heartbeat"))
        retained = [json.loads(line) for line in (args.output / "retained/received.jsonl").read_text().splitlines()]
        assert len([r for r in retained if r["retained_delivery"] and r["valid"]]) == 2
        result = {"actual_producer": True, "received": len(records), "source_comparisons": matched,
                  "streams": 5, "vm_count": 2, "reporter_did_not_advance_stalled_loop": True,
                  "retained_inventory_verified": True, "backend_storage": "not exercised",
                  "environment": "native_sim real-time mode, isolated TAP/bridge, authenticated Mosquitto 2.0.18; not Xen board",
                  **fault_result}
        (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    finally:
        for process in reversed(children):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for handle in handles:
            handle.close()
        for process in children:
            for command in commands:
                if command.get("pid") == process.pid:
                    command["rc"] = process.returncode
        (args.output / "commands.json").write_text(json.dumps(commands, indent=2) + "\n")


if __name__ == "__main__":
    main()
