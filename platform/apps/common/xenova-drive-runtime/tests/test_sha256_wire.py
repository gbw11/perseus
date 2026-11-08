"""Nano cohort용 C/Python SHA·원 DriveLink bytes 연결 시험.

SHA core는 별도 라이브러리 없이 cc로 빌드한다. protobuf는 기존 생성 schema만
사용하며 이 시험의 합성 ClockReply는 실제 장치/clock 승인 증거가 아니다.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess

import pytest

RUNTIME = Path(__file__).resolve().parents[1]
WORLD = RUNTIME.parent.parent / "drive" / "world" / "python" / "xenova_drive_world"


@pytest.fixture(scope="module")
def sha_probe(tmp_path_factory):
    build = tmp_path_factory.mktemp("sha256-wire")
    binary = build / "sha256-wire-probe"
    command = ["cc", "-std=c11", "-O2", "-g", "-Wall", "-Wextra", "-Werror",
               "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-no-pie",
               "-I" + str(RUNTIME / "include"), str(RUNTIME / "tests" / "sha256_wire_probe.c"),
               "-o", str(binary)]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:abort_on_error=1",
               UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
    return binary, build, env


def c_digest(probe, name, raw):
    binary, build, env = probe
    path = build / (name + ".bin")
    path.write_bytes(raw)
    completed = subprocess.run([str(binary), str(path)], check=True, capture_output=True,
                               text=True, timeout=15, env=env)
    assert completed.stderr == ""
    assert path.read_bytes() == raw
    digest = completed.stdout.strip()
    assert len(digest) == 64
    assert digest == hashlib.sha256(raw).hexdigest()
    return digest


def pattern(length):
    return bytes((17 * i + 0x80) & 0xff for i in range(length))


VECTORS = [("empty", b""), ("abc", b"abc"),
           *(("boundary-" + str(size), pattern(size)) for size in (55, 56, 57, 63, 64, 65)),
           ("nist-two-block-padding", b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
           ("multi-block", pattern(4096)), ("wire-cap", pattern(32768))]


@pytest.mark.parametrize("name,raw", VECTORS, ids=[case[0] for case in VECTORS])
def test_hashlib_vectors(sha_probe, name, raw):
    c_digest(sha_probe, name, raw)


def test_standard_anchor_values(sha_probe):
    assert c_digest(sha_probe, "anchor-empty", b"") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
    assert c_digest(sha_probe, "anchor-abc", b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


def test_invalid_arguments_and_input_ownership(sha_probe):
    binary, _, env = sha_probe
    result = subprocess.run([str(binary), "--invalid"], check=True, capture_output=True,
                            text=True, timeout=15, env=env)
    assert result.stdout == "invalid-arguments OK\n"
    assert result.stderr == ""


@pytest.fixture(scope="module")
def schema():
    # The actual generated file is shared with the C/Nano actor. Do not invent
    # an audit schema or import the app's test fixtures.
    spec = importlib.util.spec_from_file_location("sha256_wire_schema", WORLD / "world_pb2.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def original_reply(schema):
    host = "11111111-1111-4111-8111-111111111111"
    gateway = "22222222-2222-4222-8222-222222222222"
    device = "33333333-3333-4333-8333-333333333333"
    odom = "44444444-4444-4444-8444-444444444444"
    link = schema.DriveLink(major=2)
    reply = link.clock
    reply.probe.vm = "55555555-5555-4555-8555-555555555555"
    reply.probe.execution = "66666666-6666-4666-8666-666666666666"
    reply.probe.app_instance = "77777777-7777-4777-8777-777777777777"
    reply.probe.sequence = 9
    reply.probe.guest_send_us = 1000000
    reply.host_boot = host
    reply.gateway_instance = gateway
    reply.pi_receive_us = 2000000
    reply.pi_send_us = 2000050
    vehicle = reply.vehicle
    vehicle.major = 1
    vehicle.vehicle = "rc-car"
    vehicle.host_boot = host
    vehicle.device_session = device
    vehicle.odom_epoch = odom
    vehicle.sequence = 12
    vehicle.source_sample = 11
    vehicle.source_us = 1999900
    vehicle.pi_sample_us = 1999920
    vehicle.clock_error_us = 20
    vehicle.left_ticks = -5
    vehicle.right_ticks = 6
    vehicle.odom_pose.x = 1.0
    vehicle.odom_pose.y = 2.0
    vehicle.odom_pose.yaw = 0.1
    vehicle.velocity_mps = 0.0
    vehicle.estimated_steering_rad = 0.0
    vehicle.position_error_m = 0.01
    vehicle.heading_error_rad = 0.02
    vehicle.quality = 3
    vehicle.calibration = "a" * 64
    vehicle.gateway_instance = gateway
    vehicle.pi_session = "88888888-8888-4888-8888-888888888888"
    assert link.IsInitialized(), link.FindInitializationErrors()
    assert link.WhichOneof("payload") == "clock"
    return link


def test_original_drivelink_is_not_reserialized_before_hash(sha_probe, schema):
    link = original_reply(schema)
    canonical = link.SerializeToString(deterministic=True)
    # The existing required DriveLink.major tag1 is 08 02. Protobuf allows this
    # actual field after the clock field: semantics same, original bytes differ.
    assert canonical[:2] == b"\x08\x02"
    original = canonical[2:] + canonical[:2]
    parsed = schema.DriveLink()
    parsed.ParseFromString(original)
    assert parsed.IsInitialized() and parsed == link
    reserialized = parsed.SerializeToString(deterministic=True)
    assert reserialized == canonical and reserialized != original
    raw_hash = c_digest(sha_probe, "reply-original-reordered", original)
    canonical_hash = c_digest(sha_probe, "reply-reserialized", reserialized)
    assert raw_hash != canonical_hash
    # The receipt hashes the *entire original DriveLink*, not just ClockReply.
    nested = parsed.clock.SerializeToString(deterministic=True)
    assert raw_hash != c_digest(sha_probe, "reply-nested-only", nested)
    receipt = schema.GuestClockReceipt(probe_sequence=parsed.clock.probe.sequence,
                                      reply_sha256=bytes.fromhex(raw_hash), guest_receive_us=1000200)
    assert receipt.IsInitialized()
    assert receipt.reply_sha256 == hashlib.sha256(original).digest()
