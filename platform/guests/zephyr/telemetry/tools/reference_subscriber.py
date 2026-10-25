#!/usr/bin/env python3
"""Read-only MQTT 3.1.1 acceptance subscriber; not a backend or recovery service.

Preserves actual payload bytes and delivery metadata. Session authority comes
from an optional external bindings file, never the first packet to arrive.
Credentials stay in a private input file and process memory, not argv/logs.
"""
from __future__ import annotations
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time

BASE = Path(__file__).resolve().parents[1]


def private_json(path: Path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd) as stream:
        s = os.fstat(stream.fileno())
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid() or s.st_mode & 0o077:
            raise ValueError("private input must be owned by this UID, mode 0600")
        return json.load(stream)


def utf8(value: str):
    data = value.encode("utf-8")
    if len(data) > 65535:
        raise ValueError("MQTT string too long")
    return struct.pack("!H", len(data)) + data


def packet(kind: int, body: bytes):
    size = len(body)
    encoded = bytearray([kind])
    while True:
        b = size % 128
        size //= 128
        encoded.append(b | (128 if size else 0))
        if not size:
            return bytes(encoded) + body


class ReadOnlySubscriber:
    """Protocol operations restricted to CONNECT/SUBSCRIBE/ACK/PING/DISCONNECT."""
    def __init__(self, host, port, credentials, topic_filter="xenova/v2/vms/+/telemetry/+"):
        self.sock = socket.create_connection((host, port), timeout=3)
        self.sock.settimeout(3)
        body = utf8("MQTT") + bytes([4, 0xc2]) + struct.pack("!H", 30)
        body += utf8("xt2-reference-" + os.urandom(8).hex())
        body += utf8(credentials["username"]) + utf8(credentials["password"])
        self.sock.sendall(packet(0x10, body))
        kind, reply = self.receive()
        if kind != 0x20 or reply != b"\0\0":
            self.sock.close()
            raise ValueError("MQTT authentication rejected")
        self.sock.sendall(packet(0x82, b"\0\1" + utf8(topic_filter) + b"\1"))
        kind, reply = self.receive()
        if kind != 0x90 or reply != b"\0\1\1":
            raise ValueError("read subscription rejected")

    def exact(self, count):
        data = bytearray()
        while len(data) < count:
            part = self.sock.recv(count - len(data))
            if not part:
                raise EOFError("broker disconnected")
            data.extend(part)
        return bytes(data)

    def receive(self):
        kind = self.exact(1)[0]
        length = 0
        try:
            for i in range(4):
                b = self.exact(1)[0]
                length += (b & 127) << (7 * i)
                if length > 8192:
                    raise ValueError("bounded subscriber packet limit exceeded")
                if not b & 128:
                    return kind, self.exact(length)
        except socket.timeout as error:
            raise EOFError("partial MQTT frame timed out") from error
        raise ValueError("malformed MQTT length")

    def close(self):
        try:
            self.sock.sendall(b"\xe0\0")
        except OSError:
            pass
        self.sock.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--bindings-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--internal-plaintext", action="store_true", required=True,
                        help="explicit isolated/trusted test network; no TLS fallback")
    args = parser.parse_args(argv)
    if not 0 < args.duration <= 3600:
        parser.error("duration must be 0..3600 seconds")
    spec = importlib.util.spec_from_file_location("telemetry_oracle", BASE / "contract/tools/validate_contract.py")
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    consumer = oracle.ReferenceConsumer()
    args.output.mkdir(mode=0o700)
    subscriber = ReadOnlySubscriber(args.host, args.port, private_json(args.credentials))
    count = failures = 0
    started = time.monotonic()
    deadline = started + args.duration
    path = args.output / "received.jsonl"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as log:
            while time.monotonic() < deadline:
                try:
                    kind, data = subscriber.receive()
                except socket.timeout:
                    subscriber.sock.sendall(b"\xc0\0")
                    continue
                if kind >> 4 != 3:
                    continue
                length = int.from_bytes(data[:2], "big")
                if length > 112 or len(data) < 2 + length:
                    raise ValueError("invalid telemetry topic length")
                topic = data[2:2 + length].decode("utf-8")
                qos = (kind >> 1) & 3
                offset = 2 + length
                if qos == 1:
                    if len(data) < offset + 2:
                        raise ValueError("missing packet ID")
                    subscriber.sock.sendall(packet(0x40, data[offset:offset + 2]))
                    offset += 2
                elif qos != 0:
                    raise ValueError("unexpected QoS")
                raw = data[offset:]
                received_ms = int(time.monotonic() * 1000)
                record = {"topic": topic, "qos": qos, "retained_delivery": bool(kind & 1),
                          "dup": bool(kind & 8), "received_monotonic_ms": str(received_ms),
                          "wire_bytes": len(raw), "payload_base64": base64.b64encode(raw).decode()}
                try:
                    message = json.loads(raw.decode("utf-8"), object_pairs_hook=oracle.reject_duplicate_keys,
                                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
                    stream = message["type"].split(".")[1]
                    # MQTT 3.1.1 live forwarding clears RETAIN. A late retained
                    # inventory delivery is checked separately, never liveness.
                    oracle.validate(message, topic, qos, stream == "inventory", len(raw))
                    if args.bindings_file:
                        for binding in json.loads(args.bindings_file.read_text()):
                            consumer.bind(binding["vm_id"], binding["boot_id"], binding["publisher_instance_id"])
                    record.update(valid=True, message_id=message["message_id"],
                                  interpretation=consumer.ingest(message, received_ms, bool(kind & 1)))
                except (ValueError, KeyError, TypeError) as error:
                    failures += 1
                    record.update(valid=False, error_code=getattr(error, "code", "DECODE_INVALID"))
                log.write(json.dumps(record, ensure_ascii=False) + "\n")
                log.flush()
                count += 1
    finally:
        subscriber.close()
    summary = {"received": count, "invalid": failures, "elapsed_s": time.monotonic() - started,
               "scope": "actual subscriber bytes and semantic validation; no backend storage assertion",
               "live_forward_does_not_reveal_publish_retain": True}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))
    return 1 if failures or not count else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, EOFError):
        raise SystemExit("reference subscriber failed; inspect nonsecret connection and output state")
