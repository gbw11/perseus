"""가상 ESP32 시리얼 입력: 게이트웨이 코덱(xenova_gateway.frame)으로 만든 바이트를 그대로 받는다."""

from __future__ import annotations

import socket
import time

import pytest

from xenova_gateway.frame import FrameEncoder, StopFrame, encode
from xenova_simulator.config import SimSettings
from xenova_simulator.frames import FrameReceiver, FrameServer
from xenova_simulator.sim import Simulator


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class NullClient:
    def __init__(self) -> None:
        self.published = []
        self.subscribed = []

    def subscribe(self, topic_filter, handler=None, *, qos=1):
        self.subscribed.append(topic_filter)

    def latest(self, topic):
        return None

    def publish(self, topic, message_type, payload, **_):
        self.published.append((topic, message_type, payload))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def sim(clock):
    s = Simulator(NullClient(), SimSettings(input="frames", start_lights=False), clock=clock)
    s.subscribe()
    return s


def step(sim, clock, seconds, feed=None, dt=0.01):
    for _ in range(round(seconds / dt)):
        if feed:
            feed()
        clock.now += dt
        sim.tick(dt)


def test_frames_input_subscribes_nothing(sim):
    assert sim._client.subscribed == []


def test_no_frame_yet_means_no_motion(sim, clock):
    step(sim, clock, 0.5)
    assert sim.vehicle.v_mps == 0.0 and sim.watchdog is False


def test_drive_frame_turns_wheels_and_servo(sim, clock):
    enc = FrameEncoder()
    step(sim, clock, 2.0, feed=lambda: sim.frames.feed(encode(enc.drive(3, 0.5, 0.2)), clock.now))
    assert sim.vehicle.wheel_rpm == pytest.approx(0.5 / (2 * 3.14159265 * 0.033) * 60, rel=0.01)
    assert sim.vehicle.steer_rad == pytest.approx(0.2, abs=1e-3)  # mrad 정수로 왔다
    assert sim.applied_generation == 3


def test_stop_frame_stops_without_watchdog(sim, clock):
    enc = FrameEncoder()
    step(sim, clock, 1.0, feed=lambda: sim.frames.feed(encode(enc.drive(3, 0.5, 0.0)), clock.now))
    step(sim, clock, 1.0, feed=lambda: sim.frames.feed(encode(enc.stop(4, 1)), clock.now))
    assert sim.vehicle.v_mps == 0.0 and sim.watchdog is False and sim.applied_generation == 4


def test_watchdog_after_300ms_without_frames(sim, clock):
    enc = FrameEncoder()
    step(sim, clock, 1.0, feed=lambda: sim.frames.feed(encode(enc.drive(1, 0.5, 0.0)), clock.now))
    step(sim, clock, 0.28)  # 마지막 프레임 뒤 0.29 s (부동소수 경계 0.30 은 피한다)
    assert sim.watchdog is False
    step(sim, clock, 0.03)
    assert sim.watchdog is True
    step(sim, clock, 1.0)
    assert sim.vehicle.v_mps == 0.0
    assert sim._client.published[-1][2]["flags"]["watchdog"] is True


def test_duplicate_and_reversed_counter_are_dropped():
    r, enc = FrameReceiver(), FrameEncoder()
    first = enc.drive(5, 0.3, 0.0)
    assert r.feed(encode(first), 1.0) == 1
    assert r.feed(encode(first), 1.01) == 0          # 같은 counter
    assert r.duplicates == 1
    second, third = enc.drive(5, 0.4, 0.0), enc.drive(5, 0.5, 0.0)
    assert r.feed(encode(third) + encode(second), 1.02) == 1  # third 다음 second 는 역전
    assert r.latest().v_mps == pytest.approx(0.5)


def test_older_generation_is_dropped_and_newer_resets_counter():
    r, enc = FrameReceiver(), FrameEncoder()
    r.feed(encode(enc.drive(7, 0.3, 0.0)), 1.0)
    assert r.feed(encode(StopFrame(6, 200, 1)), 1.01) == 0
    assert r.stale_generation == 1
    assert r.feed(encode(StopFrame(8, 0, 1)), 1.02) == 1   # 새 세대는 counter 와 상관없이 기준을 새로 잡는다
    assert r.latest().stop is True and r.latest().generation == 8


def test_corrupted_frame_is_dropped_and_stream_resyncs():
    r, enc = FrameReceiver(), FrameEncoder()
    bad = bytearray(encode(enc.drive(1, 0.3, 0.0)))
    bad[3] ^= 0x55
    good = encode(enc.drive(1, 0.4, 0.0))
    assert r.feed(bytes(bad) + good, 1.0) == 1
    assert r.bad_frames == 1 and r.latest().v_mps == pytest.approx(0.4)


def test_frame_split_across_reads_is_reassembled():
    r, data = FrameReceiver(), encode(FrameEncoder().drive(2, 0.25, -0.1))
    assert r.feed(data[:5], 1.0) == 0
    assert r.feed(data[5:], 1.0) == 1
    assert r.latest().steer_rad == pytest.approx(-0.1)


def test_garbage_without_delimiter_is_discarded():
    r = FrameReceiver()
    r.feed(b"\x01" * 200, 1.0)
    assert r.bad_frames == 1
    assert r.feed(encode(FrameEncoder().drive(1, 0.1, 0.0)), 1.0) == 1


def test_tcp_server_receives_gateway_bytes():
    r = FrameReceiver()
    server = FrameServer(r, "127.0.0.1", 0)
    server.start()
    try:
        enc = FrameEncoder()
        with socket.create_connection(server.address, timeout=2) as conn:
            for i in range(5):
                conn.sendall(encode(enc.drive(9, 0.1 * i, 0.0)))
            deadline = time.monotonic() + 2
            while r.accepted < 5 and time.monotonic() < deadline:
                time.sleep(0.01)
        assert r.accepted == 5 and r.latest().v_mps == pytest.approx(0.4)
        assert server.connections == 1
    finally:
        server.stop()
