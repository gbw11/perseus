from __future__ import annotations

import io
import math
import threading

import pytest

from xenova_mqtt import Message, topics
from xenova_mqtt import envelope as env
from xenova_simulator.__main__ import handle
from xenova_simulator.config import SOURCE_ID, SimSettings
from xenova_simulator.sim import VM_APP_EVENTS, Simulator


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

    def control(self, generation, mode, active_app, active_vm_id=None):
        payload = {"generation": generation, "mode": mode, "active_app": active_app, "active_vm_id": active_vm_id}
        envelope = {"type": "vehicle.control", "payload": payload}
        self.latest_by_topic[topics.vehicle_control()] = Message(topics.vehicle_control(), envelope)

    def app(self, app_id, message_type, payload):
        topic = topics.app_events(app_id)
        self.handlers[topic](Message(topic, {"type": message_type, "payload": payload}))

    def vm_app(self, vm_id, app_id, message_type, payload):
        """공통 A/B 펌웨어(verified) 토픽. 실제 클라이언트처럼 와일드카드 구독 핸들러로 보낸다."""
        topic = topics.vm_app_events(vm_id, app_id)
        handler = next(h for f, h in self.handlers.items() if topics.matches(f, topic))
        handler(Message(topic, {"type": message_type, "payload": payload}))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def sim(client, clock):
    s = Simulator(client, SimSettings(start_lights=False), clock=clock)  # 출발 신호는 test_race.py
    s.subscribe()
    return s


def drive(sim, clock, seconds, dt=0.01, feed=None):
    for _ in range(round(seconds / dt)):
        if feed:
            feed()
        clock.now += dt
        sim.tick(dt)


def test_subscribes_control_and_both_drive_apps(client, sim):
    assert set(client.handlers) == {topics.app_events("autonomous"), topics.app_events("manual"), VM_APP_EVENTS}


def test_starts_on_track_start_pose(sim):
    v = sim.vehicle
    assert (v.x, v.y, v.heading_rad) == sim.track.start_pose()
    assert sim.track.id == "spielberg"


def test_select_track_clears_obstacles_and_goes_to_start(sim):
    sim.add_obstacle_ahead(1.0)
    sim.select_track("monza")
    assert sim.track.id == "monza" and sim.vehicle.obstacles == []
    assert (sim.vehicle.x, sim.vehicle.y, sim.vehicle.heading_rad) == sim.track.start_pose()
    _, off, progress = sim.track_frame()
    assert off == pytest.approx(0.0, abs=1e-9) and progress == pytest.approx(0.0, abs=1e-6)


def test_no_control_means_no_motion(client, clock, sim):
    drive(sim, clock, 0.5, feed=lambda: client.app("manual", "drive.command", {"generation": 0, "v_mps": 1.0, "steer_rad": 0}))
    assert sim.vehicle.v_mps == 0.0


def test_active_app_command_moves_car(client, clock, sim):
    client.control(7, "MANUAL", "manual")
    drive(sim, clock, 2.0, feed=lambda: client.app("manual", "drive.command", {"generation": 7, "v_mps": 1.0, "steer_rad": 0.0}))
    assert sim.vehicle.v_mps == pytest.approx(1.0, rel=0.01)
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
    sim.vehicle.place_obstacle_ahead(0.6)
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
    handle("o 0.6", sim, stop, out)
    assert sim.vehicle.obstacle_ahead()
    handle("c", sim, stop, out)
    assert not sim.vehicle.obstacles
    handle("e", sim, stop, out)
    assert sim.estop
    handle("e", sim, stop, out)
    assert not sim.estop
    handle("s", sim, stop, out)
    assert "gen=0" in out.getvalue() and "track=spielberg" in out.getvalue()
    handle("o 1.0 tires", sim, stop, out)
    assert sim.vehicle.obstacles[-1].kind == "tires"
    handle("t", sim, stop, out)
    assert "interlagos" in out.getvalue()
    handle("t spa", sim, stop, out)
    assert sim.track.id == "spa" and not sim.vehicle.obstacles
    handle("o x", sim, stop, out)
    assert "사용법" in out.getvalue()
    handle("q", sim, stop, out)
    assert stop.is_set()


A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_verified_command_from_active_vm_moves_car(client, clock, sim):
    client.control(4, "AUTO", "autonomous", A)
    drive(sim, clock, 1.0, feed=lambda: client.vm_app(A, "autonomous", "drive.command", {"generation": 4, "v_mps": 0.5, "steer_rad": 0.1}))
    assert sim.vehicle.v_mps > 0.4 and sim.applied_generation == 4 and sim.watchdog is False


def test_verified_command_from_other_vm_is_ignored(client, clock, sim):
    client.control(4, "AUTO", "autonomous", A)
    drive(sim, clock, 1.0, feed=lambda: client.vm_app(B, "autonomous", "drive.command", {"generation": 4, "v_mps": 0.5, "steer_rad": 0.0}))
    assert sim.vehicle.v_mps == 0.0 and sim.watchdog is True


def test_verified_readiness_on_same_topic_is_ignored(client, clock, sim):
    client.control(4, "MANUAL", "manual", B)
    client.vm_app(B, "manual", "drive.command", {"generation": 4, "v_mps": 0.3, "steer_rad": 0.0})
    client.vm_app(B, "manual", "app.readiness", {"ready": True})
    assert sim.target() == (0.3, 0.0)


def test_status_speed_comes_from_encoder(client, clock, sim):
    client.control(1, "MANUAL", "manual")
    drive(sim, clock, 2.0, feed=lambda: client.app("manual", "drive.command", {"generation": 1, "v_mps": 0.5, "steer_rad": 0.0}))
    status = client.published[-1][2]
    assert status["v_mps"] == pytest.approx(0.5, abs=0.02)
    assert sim.vehicle.encoder_count > 0


# --- 출발 신호·노면 (계약 §12 sim.pose.race·surface, S15P31E110-319) ---

@pytest.fixture
def race_sim(client, clock):
    s = Simulator(client, SimSettings(), clock=clock, rng=lambda: 0.0)   # 불이 꺼지는 순간 = 출발 후 4.9 s
    s.subscribe()
    return s


def moved(sim):
    x, y, _ = sim.track.start_pose()
    return math.hypot(sim.vehicle.x - x, sim.vehicle.y - y)


def test_autonomous_waits_on_grid_until_lights_out(client, clock, race_sim):
    client.control(3, "AUTO", "autonomous")
    feed = lambda: client.app("autonomous", "drive.command", {"generation": 3, "v_mps": 0.5, "steer_rad": 0.0})
    drive(race_sim, clock, 4.8, feed=feed)
    assert race_sim.race.phase == "lights" and race_sim.race.lights_on == 5
    assert moved(race_sim) == 0.0 and race_sim.race.jump is False
    drive(race_sim, clock, 1.0, feed=feed)
    assert race_sim.race.phase == "go" and moved(race_sim) > 0.1


def test_manual_is_not_held_and_jump_start_is_flagged(client, clock, race_sim):
    client.control(4, "MANUAL", "manual")
    drive(race_sim, clock, 1.5, feed=lambda: client.app("manual", "drive.command", {"generation": 4, "v_mps": 0.5, "steer_rad": 0.0}))
    assert race_sim.race.phase == "lights" and moved(race_sim) > 0.1
    assert race_sim.race.jump is True
    handle("r", race_sim, threading.Event(), io.StringIO())   # 다시 출발: 부정 출발 기록도 지운다
    assert race_sim.race.jump is False and moved(race_sim) == 0.0


def test_frames_input_holds_until_lights_out():
    """frames 입력은 활성 앱을 모른다 → 늘 잡아 두고 부정 출발은 판정하지 않는다."""
    from xenova_gateway.frame import FrameEncoder, encode

    clock = Clock()
    s = Simulator(FakeClient(), SimSettings(input="frames"), clock=clock, rng=lambda: 0.0)
    enc = FrameEncoder()
    drive(s, clock, 3.0, feed=lambda: s.frames.feed(encode(enc.drive(1, 0.5, 0.0)), clock.now))
    assert moved(s) == 0.0 and s.race.jump is False
    drive(s, clock, 3.0, feed=lambda: s.frames.feed(encode(enc.drive(1, 0.5, 0.0)), clock.now))
    assert s.race.phase == "go" and moved(s) > 0.1


def test_grass_caps_speed_at_0_3(client, clock, sim):
    """연석 밖(잔디·흙)은 0.3 m/s 를 넘으면 1.2 m/s² 로 줄어 0.3 m/s 에 머문다 (mock physics 와 같음)."""
    T = sim.track
    i = next(k for k in range(T.N) if T.wall_left_m[k] > 0.8 and not T.corner[k] and not T.corner[(k + 20) % T.N])
    client.control(5, "MANUAL", "manual")
    feed = lambda: client.app("manual", "drive.command", {"generation": 5, "v_mps": 1.0, "steer_rad": 0.0})
    sim.vehicle.reset(*T.at(i, 0.0), math.atan2(T.tan[i][1], T.tan[i][0]))
    sim.track_index = i
    drive(sim, clock, 1.5, feed=feed)
    assert sim.vehicle.v_mps == pytest.approx(1.0, rel=0.01)   # 아스팔트: 명령대로
    sim.push(0.55)                                             # 잔디로
    j, off, _ = sim.track_frame()
    assert T.surface(j, off) in ("grass", "dirt")
    drive(sim, clock, 0.2, feed=feed)
    assert 0.65 < sim.vehicle.v_mps < 0.85                     # 0.2 s × 1.2 m/s² 만큼 줄어드는 중
    drive(sim, clock, 0.5, feed=feed)
    j, off, _ = sim.track_frame()
    assert T.surface(j, off) in ("grass", "dirt")
    assert sim.vehicle.v_mps == pytest.approx(0.3, abs=1e-9)
