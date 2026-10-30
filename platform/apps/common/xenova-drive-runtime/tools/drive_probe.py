#!/usr/bin/env python3
"""Isolated-input/independent-receiver adapter, never a command generator.

Uses the existing v1 envelope and v2 oracle. Input and receive clocks belong to
this process; neither is subtracted from a guest clock to claim one-way latency.
Secrets come only from private files. Production Gateway/ECU is not involved.
"""
from __future__ import annotations
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import queue
import signal
import socket
import struct
import sys
import threading
import time
import types
import uuid

ROOT = Path(__file__).resolve().parents[5]
TELEMETRY = ROOT / "platform/guests/zephyr/telemetry"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Import the real transport-independent v1 contract without loading optional
# Paho. The input tool needs no new runtime dependency or replacement contract.
package = types.ModuleType("drive_contract")
package.__path__ = [str(ROOT / "platform/services/vm-agent/mqtt-client/xenova_mqtt")]
sys.modules[package.__name__] = package
v1 = load("drive_contract.envelope", Path(package.__path__[0]) / "envelope.py")
policy = load("drive_contract.policy", Path(package.__path__[0]) / "policy.py")
reference = load("drive_reference", TELEMETRY / "tools/reference_subscriber.py")
oracle = load("drive_v2_oracle", TELEMETRY / "contract/tools/validate_contract.py")


class Peer(reference.ReadOnlySubscriber):
    """Small test adapter reusing the reference subscriber's bounded framing."""
    def __init__(self, host, port, credential, topic_filter=None):
        self.sock = socket.create_connection((host, port), timeout=3)
        self.sock.settimeout(1)
        body = reference.utf8("MQTT") + bytes([4, 0xc2]) + struct.pack("!H", 30)
        body += reference.utf8("drive-test-" + uuid.uuid4().hex)
        body += reference.utf8(credential["username"]) + reference.utf8(credential["password"])
        self.sock.sendall(reference.packet(0x10, body))
        if self.receive() != (0x20, b"\0\0"):
            self.sock.close()
            raise ValueError("isolated broker authentication rejected")
        if topic_filter:
            self.sock.sendall(reference.packet(0x82, b"\0\1" + reference.utf8(topic_filter) + b"\1"))
            if self.receive() != (0x90, b"\0\1\1"):
                self.sock.close()
                raise ValueError("isolated subscription rejected")
        self.packet_id = 1

    def publish(self, topic, message):
        rule = policy.for_type(message["type"])
        raw = json.dumps(message, separators=(",", ":"), allow_nan=False).encode()
        v1.validate(topic, message)
        if len(raw) > rule.max_bytes:
            raise ValueError("input exceeds existing contract size limit")
        body = reference.utf8(topic)
        if rule.qos:
            self.packet_id = self.packet_id % 65535 + 1
            body += struct.pack("!H", self.packet_id)
        self.sock.sendall(reference.packet(0x30 | rule.qos << 1 | int(rule.retain), body + raw))
        if rule.qos and self.receive() != (0x40, struct.pack("!H", self.packet_id)):
            raise ValueError("input PUBACK not received")


def scenario(index):
    """Fixed 8-second sequence, 20Hz input; no new driving calculation here."""
    phase = (index // 40) % 4
    controls = {"generation": 100 + phase, "mode": "AUTO" if phase < 2 else "MANUAL",
                "active_app": "autonomous" if phase < 2 else "manual"}
    keys = [["up"], ["up", "left"], ["down", "right"], ["brake"]][phase]
    perception = {"lane_offset_m": [0, 0.1, -0.1, 0][phase], "heading_err_rad": 0,
                  "stop": phase == 3, "obstacles": []}
    return controls, {"keys": keys}, perception


def receive(host, port, credential, topic_filter, output, duration, bindings, stop=None):
    """Timestamp before JSON/validation; bounded queue isolates disk/analysis."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    records = queue.Queue(maxsize=4096)
    stop = stop if stop is not None else threading.Event()
    stats = {"received": 0, "queue_dropped": 0, "invalid": 0, "reader_errors": 0,
             "queue_capacity": 4096, "writer_errors": 0,
             "retired_or_unbound": 0, "historical_inventory": 0,
             "pc_affinity": sorted(os.sched_getaffinity(0)), "clock": "receiver_monotonic_ns"}

    def write():
        consumer = oracle.ReferenceConsumer()
        for binding in bindings.values():
            consumer.bind(binding["vm_id"], binding["boot_id"], binding["publisher_instance_id"])
        try:
            with output.open("x", encoding="utf-8") as stream:
                while not stop.is_set() or not records.empty():
                    try:
                        record = records.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    try:
                        message = json.loads(base64.b64decode(record["payload_base64"]),
                                             object_pairs_hook=oracle.reject_duplicate_keys)
                        if message.get("schema_version") == "2.0":
                            kind = message["type"].split(".")[-1]
                            oracle.validate(message, record["topic"], record["qos"], kind == "inventory", record["wire_bytes"])
                            expected = bindings.get(message["vm_id"])
                            record["binding_matches"] = bool(expected and all(
                                message[k] == expected[k] for k in ("boot_id", "publisher_instance_id")))
                            record["interpretation"] = consumer.ingest(message,
                                int(record["received_monotonic_ns"]) // 1_000_000, record["retained_delivery"])
                            if not record["binding_matches"]:
                                stats["retired_or_unbound"] += 1
                            if record["interpretation"] == "HISTORICAL_ONLY":
                                stats["historical_inventory"] += 1
                            if record["interpretation"] in {"RETAIN_VIOLATION", "ID_REUSE_CONFLICT", "SNAPSHOT_CONFLICT"}:
                                raise ValueError("v2 state contract violation")
                        else:
                            v1.validate(record["topic"], message)
                            rule = policy.for_type(message["type"])
                            if record["qos"] != rule.qos or record["wire_bytes"] > rule.max_bytes:
                                raise ValueError("v1 delivery policy mismatch")
                        record["valid"] = True
                    except (ValueError, KeyError, TypeError, oracle.ContractError) as error:
                        stats["invalid"] += 1
                        record.update(valid=False, error=type(error).__name__)
                    stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError:
            stats["writer_errors"] += 1
            stop.set()

    writer = threading.Thread(target=write, name="bounded-drive-writer")
    writer.start()
    deadline = time.monotonic() + duration
    try:
        while time.monotonic() < deadline and not stop.is_set():
            peer = None
            try:
                peer = Peer(host, port, credential, topic_filter)
                while time.monotonic() < deadline and not stop.is_set():
                    try:
                        kind, data = peer.receive()
                    except socket.timeout:
                        peer.sock.sendall(b"\xc0\0")
                        continue
                    rx_ns = time.monotonic_ns()
                    if kind >> 4 != 3:
                        continue
                    length = int.from_bytes(data[:2], "big")
                    topic = data[2:2 + length].decode()
                    qos = kind >> 1 & 3
                    offset = 2 + length
                    if qos == 1:
                        peer.sock.sendall(reference.packet(0x40, data[offset:offset + 2]))
                        offset += 2
                    elif qos:
                        raise ValueError("unsupported receiver QoS")
                    raw = data[offset:]
                    record = {"received_monotonic_ns": str(rx_ns), "topic": topic, "qos": qos,
                              "retained_delivery": bool(kind & 1), "dup": bool(kind & 8),
                              "wire_bytes": len(raw), "payload_base64": base64.b64encode(raw).decode()}
                    stats["received"] += 1
                    try:
                        records.put_nowait(record)
                    except queue.Full:
                        stats["queue_dropped"] += 1
            except (OSError, EOFError, ValueError):
                stats["reader_errors"] += 1
                time.sleep(0.1)
            finally:
                if peer:
                    peer.close()
    finally:
        stats["stop_requested"] = stop.is_set()
        stop.set()
        writer.join(timeout=5)
        if writer.is_alive():
            raise RuntimeError("bounded writer did not stop")
        output.with_suffix(".summary.json").write_text(json.dumps(stats, indent=2) + "\n")
    return stats


def send_inputs(host, port, credential, output, duration):
    identity = v1.Identity(source_id="dom0-server")
    perception_identity = v1.Identity(source_id="jetson", app_id="perception")
    topics = v1.topics
    peer = None
    started = time.monotonic()
    index = sent = failed = 0
    with Path(output).open("x", encoding="utf-8") as stream:
        while time.monotonic() - started < duration:
            due = started + index * 0.05
            time.sleep(max(0, due - time.monotonic()))
            control, keys, perception = scenario(index)
            for topic, kind, payload, who in (
                (topics.vehicle_control(), "vehicle.control", control, identity),
                (topics.manual_input(), "drive.input", keys, identity),
                (topics.app_events("perception"), "perception.result", perception, perception_identity)):
                message = v1.build(who, kind, payload, index + 1)
                rc = 0
                try:
                    if not peer:
                        peer = Peer(host, port, credential)
                    peer.publish(topic, message)
                    sent += 1
                except (OSError, EOFError, ValueError):
                    failed += 1
                    rc = -1
                    if peer:
                        peer.close()
                        peer = None
                stream.write(json.dumps({"input_index": index, "sent_monotonic_ns": str(time.monotonic_ns()),
                                         "topic": topic, "message": message, "rc": rc}) + "\n")
            index += 1
            # Keep the frozen input sequence; expired slots are skipped, not burst.
            index = max(index, int((time.monotonic() - started) / 0.05))
    if peer:
        peer.close()
    return {"sent": sent, "failed": failed, "scenario": "fixed-four-phase-20Hz-v1"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("input", "receive"))
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--credentials", type=Path, required=True)
    p.add_argument("--bindings", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--duration", type=float, default=30)
    p.add_argument("--filter", default="xenova/#")
    p.add_argument("--isolated-test-network", action="store_true", required=True)
    args = p.parse_args()
    if not 0 < args.duration <= 3600 or not 1 <= args.port <= 65535:
        p.error("bounded duration and valid port required")
    credentials = reference.private_json(args.credentials)
    bindings = {b["vm_id"]: b for b in json.loads(args.bindings.read_text())} if args.bindings else {}
    if args.mode == "input":
        result = send_inputs(args.host, args.port, credentials, args.output, args.duration)
    else:
        stop = threading.Event()
        previous = signal.signal(signal.SIGTERM, lambda _signum, _frame: stop.set())
        try:
            result = receive(args.host, args.port, credentials, args.filter, args.output, args.duration, bindings, stop)
        finally:
            signal.signal(signal.SIGTERM, previous)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
