"""실제 Mosquitto 로 도는 통합 테스트. 브로커가 없으면 건너뛴다 (mqtt-client 테스트와 같은 방식).

    docker run --rm -d --name xenova-mq-test -p 18830:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf
    XENOVA_MQTT_TEST_BROKER=127.0.0.1:18830 XENOVA_MQTT_TEST_CONTAINER=xenova-mq-test python -m pytest
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import uuid

import pytest

from xenova_mqtt import Identity, XenovaClient, topics
from xenova_manual.__main__ import build_client
from xenova_manual.app import COMMAND_TOPIC, ManualDriveApp
from xenova_manual.config import ManualSettings

BROKER = os.environ.get("XENOVA_MQTT_TEST_BROKER", "")
CONTAINER = os.environ.get("XENOVA_MQTT_TEST_CONTAINER", "")

pytestmark = pytest.mark.skipif(not BROKER, reason="XENOVA_MQTT_TEST_BROKER 미설정")


def _host_port() -> tuple[str, int]:
    host, port = BROKER.rsplit(":", 1)
    return host, int(port)


def _peer(source_id: str) -> XenovaClient:
    host, port = _host_port()
    client = XenovaClient(Identity(source_id=source_id), host, port)
    assert client.start(wait_s=5), "브로커 연결 실패"
    return client


class Recorder:
    """drive.command 를 받은 단조 시각과 함께 모은다."""

    def __init__(self) -> None:
        self.items: list[tuple[float, dict]] = []
        self.client = _peer("test-recorder")
        self.client.subscribe(COMMAND_TOPIC, lambda m: self.items.append((m.received_monotonic, m.envelope)), qos=0)
        time.sleep(0.3)

    def window(self, seconds: float, message_type: str = "drive.command") -> list[tuple[float, dict]]:
        start = len(self.items)
        time.sleep(seconds)
        return [item for item in self.items[start:] if item[1]["type"] == message_type]

    def commands(self) -> list[tuple[float, dict]]:
        return [item for item in self.items if item[1]["type"] == "drive.command"]


@pytest.fixture
def stack():
    host, port = _host_port()
    settings = ManualSettings(vm_id=str(uuid.uuid4()), broker_host=host, broker_port=port, username=None)
    gateway, backend = _peer("dom0-gateway"), _peer("dom0-server")
    gateway.clear_retained(topics.vehicle_control())
    recorder = Recorder()
    client = build_client(settings)
    app = ManualDriveApp(client, settings)
    app.subscribe()
    assert client.start(wait_s=5)
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(stop,), daemon=True)
    thread.start()
    time.sleep(0.3)
    yield settings, gateway, backend, recorder
    stop.set()
    thread.join(timeout=1)
    gateway.clear_retained(topics.vehicle_control())
    for c in (client, gateway, backend, recorder.client):
        c.stop()


def _press(backend: XenovaClient, keys: list[str], seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        backend.publish(topics.manual_input(), "drive.input", {"keys": keys})
        time.sleep(0.05)


def test_e2_e8_idle_publishes_50hz_stop_generation_0(stack):
    settings, _, _, recorder = stack
    items = recorder.window(2.0)
    assert 90 <= len(items) <= 110, len(items)
    for _, envelope in items:
        assert envelope["type"] == "drive.command"
        assert envelope["app_id"] == "manual" and envelope["vm_id"] == settings.vm_id
        assert envelope["payload"] == {"generation": 0, "v_mps": 0.0, "steer_rad": 0.0}


def test_e3_follows_control_generation(stack):
    _, gateway, _, recorder = stack
    gateway.publish(topics.vehicle_control(), "vehicle.control",
                    {"generation": 7, "mode": "MANUAL", "active_app": "manual", "active_vm_id": None, "reason": "test"})
    sent = time.monotonic()
    time.sleep(0.3)
    first = next(t for t, e in recorder.commands() if e["payload"]["generation"] == 7)
    assert first - sent < 0.1
    assert all(e["payload"]["generation"] == 7 for t, e in recorder.commands() if t > first)


def test_e4_e5_e6_drive_then_stop_after_release(stack):
    _, _, backend, recorder = stack
    _press(backend, ["up"], 0.5)
    assert recorder.commands()[-1][1]["payload"] == {"generation": 0, "v_mps": 1.0, "steer_rad": 0.0}
    _press(backend, ["up", "left"], 0.5)
    assert recorder.commands()[-1][1]["payload"] == {"generation": 0, "v_mps": 1.0, "steer_rad": 0.4}
    released = time.monotonic()
    time.sleep(0.5)
    stopped = next(t for t, e in recorder.commands() if t > released and e["payload"]["v_mps"] == 0.0)
    # 마지막 입력(≤ 50 ms 전) + 타임아웃 200 ms + 한 주기 20 ms
    assert stopped - released <= 0.22 + 0.05, stopped - released


@pytest.mark.skipif(not CONTAINER, reason="XENOVA_MQTT_TEST_CONTAINER 미설정")
def test_e7_survives_broker_restart(stack):
    _, _, _, recorder = stack
    subprocess.run(["docker", "restart", "-t", "0", CONTAINER], check=True, capture_output=True)
    time.sleep(12)  # 재연결 지연 최대 10초 (xenova_mqtt)
    assert len(recorder.window(1.0)) >= 40
