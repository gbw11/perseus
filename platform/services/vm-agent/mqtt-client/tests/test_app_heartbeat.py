"""app.heartbeat 도우미와 systemd 통지 (MQTT 계약 6.1, S15P31E110-301)."""

from __future__ import annotations

import os
import re
import socket
import tempfile
from pathlib import Path

import pytest

import xenova_mqtt
from xenova_mqtt import systemd
from xenova_mqtt.app import AppHeartbeat


class FakeClient:
    def __init__(self):
        self.connected = True
        self.published = []

    def publish(self, topic, message_type, payload, **kwargs):
        self.published.append((topic, message_type, payload))
        return {}


def make(watchdog_sec=2.0):
    t = [100.0]
    pings = []
    client = FakeClient()
    hb = AppHeartbeat(client, "my-app", clock=lambda: t[0], watchdog=lambda: pings.append(t[0]) or True,
                      watchdog_interval_s=watchdog_sec)
    return client, hb, t, pings


def test_beat_from_the_loop_publishes_at_most_once_per_period():
    client, hb, t, _ = make()
    assert hb.beat("idle") is True
    for _ in range(3):  # 250ms 루프
        t[0] += 0.25
        assert hb.beat("idle") is False
    t[0] += 0.25
    assert hb.beat("active") is True
    assert [(p[0], p[1]) for p in client.published] == [("xenova/v1/apps/my-app/events", "app.heartbeat")] * 2
    assert client.published[0][2] == {"state": "idle", "uptime_s": 0.0} and client.published[1][2] == {"state": "active", "uptime_s": 1.0}


def test_disconnected_beats_are_not_queued_and_resume_immediately():
    client, hb, t, _ = make()
    hb.beat("idle")
    client.connected = False
    for _ in range(5):
        t[0] += 1
        assert hb.beat("idle") is False
    assert hb.skipped == 5 and len(client.published) == 1  # 끊긴 동안 쌓지 않는다
    client.connected = True
    t[0] += 0.2
    assert hb.beat("idle") is True and client.published[-1][2]["uptime_s"] == 5.2


def test_skipped_counts_missed_periods_not_loop_iterations():
    client, hb, t, _ = make()
    hb.beat("idle")
    client.connected = False
    for _ in range(250):  # 20ms 루프로 5초
        t[0] += 0.02
        hb.beat("idle")
    assert hb.skipped == 4  # 1초가 지난 뒤부터 1초에 한 번
    client.connected = True
    t[0] += 0.02
    assert hb.beat("idle") is True  # 재연결 직후 바로 낸다


def test_watchdog_is_pinged_even_when_the_broker_is_down():
    client, hb, t, pings = make(watchdog_sec=2.0)
    client.connected = False
    for _ in range(4):
        hb.beat("idle")
        t[0] += 1
    assert pings == [100.0, 101.0, 102.0, 103.0]  # WatchdogSec 2초 → 1초마다. 브로커가 죽었다고 systemd 가 앱을 죽이면 안 된다


@pytest.mark.parametrize("watchdog_sec", [1.0, 5.0, 10.0])
def test_watchdog_follows_half_of_watchdog_sec_and_mqtt_stays_at_one_second(watchdog_sec):
    client, hb, t, pings = make(watchdog_sec=watchdog_sec)
    for _ in range(int(20 / 0.25)):  # 250ms 루프로 20초
        hb.beat("idle")
        t[0] += 0.25
    gaps = [b - a for a, b in zip(pings, pings[1:])]
    assert gaps and max(gaps) <= watchdog_sec / 2  # WatchdogSec 안에 반드시 두 번 이상 통지
    assert hb.watchdog_every_s == watchdog_sec / 2
    assert len(client.published) == 20  # MQTT 는 WatchdogSec 과 무관하게 계약값 1초


def test_without_watchdog_sec_no_notification_is_sent():
    client, hb, t, pings = make(watchdog_sec=None)
    for _ in range(5):
        hb.beat("idle")
        t[0] += 1
    assert pings == [] and hb.watchdog_every_s is None and len(client.published) == 5


def test_watchdog_interval_comes_from_the_unit_environment(monkeypatch):
    monkeypatch.setenv("WATCHDOG_USEC", "8000000")
    monkeypatch.delenv("WATCHDOG_PID", raising=False)
    assert AppHeartbeat(FakeClient(), "my-app", watchdog=lambda: True).watchdog_every_s == 4.0
    monkeypatch.delenv("WATCHDOG_USEC")
    assert AppHeartbeat(FakeClient(), "my-app", watchdog=lambda: True).watchdog_every_s is None
    with pytest.raises(ValueError):
        AppHeartbeat(FakeClient(), "my-app", watchdog_interval_s=0)


def test_package_version_matches_pyproject():
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1) == xenova_mqtt.__version__ == "0.2.0"


def test_unknown_state_is_rejected():
    _, hb, _, _ = make()
    with pytest.raises(ValueError):
        hb.beat("running")


def test_notify_without_socket_is_a_noop_and_never_raises():
    assert systemd.notify("READY=1", env={}) is False
    assert systemd.watchdog(env={"NOTIFY_SOCKET": "relative/path"}) is False

    def broken():
        raise OSError("no unix sockets here")

    assert systemd.notify("READY=1", env={"NOTIFY_SOCKET": "/run/x"}, factory=broken) is False


def test_notify_sends_the_state_and_maps_abstract_sockets():
    sent = []

    class Sock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def sendto(self, data, address):
            sent.append((data, address))

    assert systemd.watchdog(env={"NOTIFY_SOCKET": "@xenova/notify"}, factory=Sock) is True
    assert systemd.ready(env={"NOTIFY_SOCKET": "/run/systemd/notify"}, factory=Sock) is True
    assert sent == [(b"WATCHDOG=1", "\0xenova/notify"), (b"READY=1", "/run/systemd/notify")]


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or os.name == "nt", reason="unix datagram sockets")
def test_notify_reaches_a_real_unix_datagram_socket():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "notify")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(path)
        try:
            assert systemd.watchdog(env={"NOTIFY_SOCKET": path}) is True
            assert server.recv(64) == b"WATCHDOG=1"
        finally:
            server.close()


def test_watchdog_interval_is_read_for_this_process_only():
    assert systemd.watchdog_interval_s({"WATCHDOG_USEC": "10000000"}) == 10.0
    assert systemd.watchdog_interval_s({"WATCHDOG_USEC": "10000000", "WATCHDOG_PID": str(os.getpid())}) == 10.0
    assert systemd.watchdog_interval_s({"WATCHDOG_USEC": "10000000", "WATCHDOG_PID": "1"}) is None
    assert systemd.watchdog_interval_s({}) is None and systemd.watchdog_interval_s({"WATCHDOG_USEC": "x"}) is None
