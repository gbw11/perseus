from __future__ import annotations

import io
import threading

import pytest

from xenova_mqtt import Message, topics
from xenova_mqtt import envelope as env
from xenova_simulator.__main__ import handle
from xenova_simulator.config import SOURCE_ID, SimSettings
from xenova_simulator.sim import Simulator


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeClient:
    def __init__(self) -> None:
        self.latest_by_topic: dict[str, Message] = {}
        self.handlers: dict[str, object] = {}
        self.published: list[tuple[str, str, dict]] = []

    def subscribe(self, topic_filter, handler=None, *, qos=1):
        if handler:
            self.handlers[topic_filter] = handler

    def latest(self, topic):
        return self.latest_by_topic.get(topic)

    def publish(self, topic, message_type, payload, **_):
        self.published.append((topic, message_type, payload))

    def control(self, generation, mode, active_app):
        envelope = {"type": "vehicle.control", "payload": {"generation": generation, "mode": mode, "active_app": active_app}}
        self.latest_by_topic[topics.vehicle_control()] = Message(topics.vehicle_control(), envelope)

    def app(self, app_id, message_type, payload):
        topic = topics.app_events(app_id)
        self.handlers[topic](Message(topic, {"type": message_type, "payload": payload}))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def sim(client, clock):
    s = Simulator(client, SimSettings(), clock=clock)
    s.subscribe()
    return s


def drive(sim, clock, seconds, dt=0.01, feed=None):
    for _ in range(round(seconds / dt)):
        if feed:
            feed()
        clock.now += dt
        sim.tick(dt)


def test_subscribes_control_and_both_drive_apps(client, sim):
    assert set(client.handlers) == {topics.app_events("autonomous"), topics.app_events("manual")}


def test_no_control_means_no_motion(client, clock, sim):
    drive(sim, clock, 0.5, feed=lambda: client.app("manual", "drive.command", {"generation": 0, "v_mps": 1.0, "steer_rad": 0}))
    assert sim.vehicle.v_mps == 0.0


def test_active_app_command_moves_car(client, clock, sim):
    client.control(7, "MANUAL", "manual")
    drive(sim, clock, 1.0, feed=lambda: client.app("manual", "drive.command", {"generation": 7, "v_mps": 1.0, "steer_rad": 0.0}))
    assert sim.vehicle.v_mps == pytest.approx(1.0)
    assert sim.applied_generation == 7
    assert sim.watchdog is False


def test_standby_app_command_is_ignored(client, clock, sim):
    client.control(3, "AUTO", "autonomous")
    drive(sim, clock, 0.5, feed=lambda: client.app("manual", "drive.command", {"generation": 3, "v_mps": 1.0, "steer_rad": 0.0}))
    assert sim.vehicle.v_mps == 0.0
    assert sim.watchdog is True  # 활성 앱(autonomous) 명령이 없다


def test_wrong_generation_is_ignored(client, clock, sim):
    client.control(5, "MANUAL", "manual")
    drive(sim, clock, 0.5, feed=lambda: client.app("manual", "drive.command", {"generation": 4, "v_mps": 1.0, "steer_rad": 0.0}))
    assert sim.vehicle.v_mps == 0.0 and sim.applied_generation == 0


def test_heartbeat_on_same_topic_does_not_replace_command(client, clock, sim):
    client.control(1, "MANUAL", "manual")
    client.app("manual", "drive.command", {"generation": 1, "v_mps": 0.8, "steer_rad": 0.0})
    client.app("manual", "app.heartbeat", {"state": "active", "uptime_s": 1.0})
    assert sim.target() == (0.8, 0.0)


def test_watchdog_after_300ms_without_command(client, clock, sim):
    client.control(2, "MANUAL", "manual")
    drive(sim, clock, 1.0, feed=lambda: client.app("manual", "drive.command", {"generation": 2, "v_mps": 1.0, "steer_rad": 0.0}))
    drive(sim, clock, 0.29)
    assert sim.watchdog is False
    drive(sim, clock, 0.02)
    assert sim.watchdog is True
    drive(sim, clock, 1.0)
    assert sim.vehicle.v_mps == 0.0
    assert client.published[-1][2]["flags"]["watchdog"] is True


def test_safe_stop_stops_without_watchdog(client, clock, sim):
    client.control(9, "SAFE_STOP", None)
    drive(sim, clock, 0.5)
    assert sim.vehicle.v_mps == 0.0 and sim.watchdog is False


def test_estop_stops_and_flags(client, clock, sim):
    client.control(1, "MANUAL", "manual")
    feed = lambda: client.app("manual", "drive.command", {"generation": 1, "v_mps": 1.0, "steer_rad": 0.0})  # noqa: E731
    drive(sim, clock, 1.0, feed=feed)
    sim.estop = True
    drive(sim, clock, 1.0, feed=feed)
    assert sim.vehicle.v_mps == 0.0
    assert client.published[-1][2]["flags"]["estop"] is True


def test_obstacle_flag_in_status(client, clock, sim):
    sim.vehicle.place_obstacle_ahead(0.3)
    drive(sim, clock, 0.1)
    assert client.published[-1][2]["flags"]["obstacle"] is True


def test_status_rate_is_20hz(client, clock, sim):
    drive(sim, clock, 1.0)
    statuses = [p for t, ty, p in client.published if ty == "vehicle.status"]
    assert 19 <= len(statuses) <= 21
    assert all(t == topics.vehicle_status() for t, _, _ in client.published)


def test_status_envelope_passes_contract_validation(sim):
    payload = sim.status()
    envelope = env.build(env.Identity(source_id=SOURCE_ID), "vehicle.status", payload, 1)
    env.validate(topics.vehicle_status(), envelope)
    assert envelope["source_id"] == "dom0-gateway"
    assert set(payload) == {"applied_generation", "v_mps", "steer_rad", "flags", "batt_v"}
    assert set(payload["flags"]) == {"estop", "watchdog", "obstacle"}


def test_malformed_command_is_dropped(client, clock, sim):
    client.control(1, "MANUAL", "manual")
    client.app("manual", "drive.command", {"generation": "1", "v_mps": 1.0, "steer_rad": 0.0})
    client.app("manual", "drive.command", {"generation": 1, "v_mps": True, "steer_rad": 0.0})
    assert sim.target() == (0.0, 0.0) and sim.watchdog is True


def test_terminal_commands(sim):
    out, stop = io.StringIO(), threading.Event()
    handle("o 0.3", sim, stop, out)
    assert sim.vehicle.obstacle_ahead()
    handle("c", sim, stop, out)
    assert not sim.vehicle.obstacles
    handle("e", sim, stop, out)
    assert sim.estop
    handle("e", sim, stop, out)
    assert not sim.estop
    handle("s", sim, stop, out)
    assert "gen=0" in out.getvalue()
    handle("o x", sim, stop, out)
    assert "사용법" in out.getvalue()
    handle("q", sim, stop, out)
    assert stop.is_set()
