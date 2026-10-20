from __future__ import annotations

import threading
import time

import pytest

from xenova_mqtt import Message, topics
from xenova_manual.app import COMMAND_TOPIC, ManualDriveApp
from xenova_manual.config import ManualSettings

VM_ID = "22222222-2222-4222-8222-222222222222"


class FakeClient:
    def __init__(self) -> None:
        self.latest_by_topic: dict[str, Message] = {}
        self.published: list[tuple[str, str, dict]] = []
        self.subscribed: list[str] = []

    def subscribe(self, topic_filter, handler=None, *, qos=1):
        self.subscribed.append(topic_filter)

    def latest(self, topic):
        return self.latest_by_topic.get(topic)

    def publish(self, topic, message_type, payload, **_):
        self.published.append((topic, message_type, payload))

    def of_type(self, message_type):
        return [payload for _, t, payload in self.published if t == message_type]

    def put(self, topic, message_type, payload, *, age_ms=0.0):
        envelope = {"type": message_type, "payload": payload}
        self.latest_by_topic[topic] = Message(topic, envelope, received_monotonic=time.monotonic() - age_ms / 1000)


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def app(client):
    return ManualDriveApp(client, ManualSettings(vm_id=VM_ID))


def test_subscribes_to_control_and_input(app, client):
    app.subscribe()
    assert set(client.subscribed) == {topics.vehicle_control(), topics.manual_input()}


def test_no_control_no_input_publishes_stop_with_generation_0(app, client):
    assert app.tick() == {"generation": 0, "v_mps": 0.0, "steer_rad": 0.0}
    assert client.published[0] == (COMMAND_TOPIC, "drive.command", {"generation": 0, "v_mps": 0.0, "steer_rad": 0.0})
    assert COMMAND_TOPIC == "xenova/v1/apps/manual/events"


def test_uses_latest_control_generation(app, client):
    client.put(topics.vehicle_control(), "vehicle.control", {"generation": 7, "mode": "MANUAL", "active_app": "manual"})
    assert app.tick()["generation"] == 7
    client.put(topics.vehicle_control(), "vehicle.control", {"generation": 8, "mode": "AUTO", "active_app": "autonomous"})
    assert app.tick()["generation"] == 8


@pytest.mark.parametrize("bad", [None, -1, "7", 1.5, True])
def test_invalid_generation_falls_back_to_0(app, client, bad):
    client.put(topics.vehicle_control(), "vehicle.control", {"generation": bad})
    assert app.tick()["generation"] == 0


def test_fresh_input_drives(app, client):
    client.put(topics.manual_input(), "drive.input", {"keys": ["up", "left"]}, age_ms=10)
    assert app.tick() == {"generation": 0, "v_mps": 1.0, "steer_rad": 0.4}


def test_stale_input_stops(app, client):
    client.put(topics.manual_input(), "drive.input", {"keys": ["up"]}, age_ms=250)
    assert app.tick()["v_mps"] == 0.0


@pytest.mark.parametrize("payload", [{}, {"keys": "up"}, {"keys": None}])
def test_malformed_input_stops(app, client, payload):
    client.put(topics.manual_input(), "drive.input", payload)
    assert app.tick()["v_mps"] == 0.0


def test_wrong_type_on_input_topic_is_ignored(app, client):
    client.put(topics.manual_input(), "drive.command", {"keys": ["up"]})
    assert app.tick()["v_mps"] == 0.0


def test_keeps_publishing_while_standby(app, client):
    # 대기(AUTO) 중에도 발행한다. 게이트웨이가 신선도 확인에 쓴다 (계약 5.2)
    client.put(topics.vehicle_control(), "vehicle.control", {"generation": 3, "mode": "AUTO", "active_app": "autonomous"})
    for _ in range(5):
        app.tick()
    assert len(client.of_type("drive.command")) == 5


def test_run_rate_is_about_50hz(client):
    app = ManualDriveApp(client, ManualSettings(vm_id=VM_ID))
    stop = threading.Event()
    thread = threading.Thread(target=app.run, args=(stop,))
    thread.start()
    time.sleep(1.0)
    stop.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
    # CI 부하를 감안한 폭. 정밀한 주기는 브로커 테스트(2초 90~110건)가 본다
    assert 40 <= len(client.of_type("drive.command")) <= 60
    assert 1 <= len(client.of_type("app.heartbeat")) <= 2


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_heartbeat_every_second_on_same_topic(client):
    clock = FakeClock()
    app = ManualDriveApp(client, ManualSettings(vm_id=VM_ID), clock=clock)
    for i in range(120):  # 50 Hz 로 2.4초. 누적 덧셈 오차를 피하려고 곱으로 시각을 정한다
        clock.now = 100.0 + i * 0.02
        app.tick()
    beats = [(topic, payload) for topic, t, payload in client.published if t == "app.heartbeat"]
    assert len(beats) == 3  # 0초, 1초, 2초
    assert all(topic == COMMAND_TOPIC for topic, _ in beats)
    assert [p["uptime_s"] for _, p in beats] == pytest.approx([0.0, 1.0, 2.0], abs=0.03)


@pytest.mark.parametrize(
    ("control", "state"),
    [
        (None, "idle"),
        ({"generation": 3, "mode": "MANUAL", "active_app": "manual"}, "active"),
        ({"generation": 3, "mode": "AUTO_READY", "active_app": "manual"}, "active"),
        ({"generation": 3, "mode": "AUTO", "active_app": "autonomous"}, "standby"),
        ({"generation": 3, "mode": "SAFE_STOP", "active_app": None}, "standby"),
        ({"generation": "bad"}, "idle"),
    ],
)
def test_heartbeat_state_follows_vehicle_control(app, client, control, state):
    if control is not None:
        client.put(topics.vehicle_control(), "vehicle.control", control)
    assert app.heartbeat()["state"] == state
