"""시뮬레이터 전용 토픽 (계약 §12): sim.pose·sim.world 발행, sim.command 적용, sim.command.result."""

from __future__ import annotations

import json
import math

import pytest

from xenova_mqtt import Message, topics
from xenova_mqtt import envelope as env
from xenova_simulator.config import SimSettings
from xenova_simulator.sim import Simulator
from xenova_simulator.simtopics import SOURCE_ID, SimTopics


class Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


class FakeClient:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}
        self.published: list[tuple[str, str, dict, dict]] = []
        self.raw: list[tuple[str, bytes, bool]] = []

    def subscribe(self, topic_filter, handler=None, *, qos=1):
        self.handlers[topic_filter] = handler

    def latest(self, topic):
        return None

    def publish(self, topic, message_type, payload, **kw):
        self.published.append((topic, message_type, payload, kw))

    def publish_raw(self, topic, data, *, qos, retain, wait_s=None):
        self.raw.append((topic, data, retain))

    def command(self, payload):
        self.handlers[topics.sim_command()](Message(topics.sim_command(), {"type": "sim.command", "payload": payload}))

    def of(self, message_type):
        return [p for t, ty, p, _ in self.published if ty == message_type]


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def setup(clock):
    sim = Simulator(FakeClient(), SimSettings(camera_listen=None), clock=clock)
    client = FakeClient()
    st = SimTopics(sim, client, clock=clock)
    st.subscribe()
    return sim, client, st


def ahead(sim, d, side=0.0):
    v = sim.vehicle
    h = v.heading_rad
    return v.x + d * math.cos(h) - side * math.sin(h), v.y + d * math.sin(h) + side * math.cos(h)


def test_subscribes_only_sim_command(setup):
    _, client, _ = setup
    assert list(client.handlers) == [topics.sim_command()]


def test_world_is_retained_once_until_something_changes(setup):
    sim, client, st = setup
    st.publish()
    st.publish()
    worlds = [(p, kw) for t, ty, p, kw in client.published if ty == "sim.world"]
    assert len(worlds) == 1
    world, kw = worlds[0]
    assert kw == {"qos": 1, "retain": True}
    assert world["revision"] == 1
    assert world["track"]["id"] == "spielberg" and world["track"]["shape"] == "centerline"
    assert len(world["tracks"]) == 16 and world["car"] == {"length_m": 0.42, "width_m": 0.2}
    assert world["obstacles"] == [] and world["estop"] is False
    sim.add_obstacle_ahead(2.0, "box")  # 터미널 명령도 world 를 바꾼다
    st.publish()
    world = client.of("sim.world")[-1]
    assert world["revision"] == 2 and world["obstacles"][0]["kind"] == "box"


def test_pose_at_20hz_with_surface(setup, clock):
    sim, client, st = setup
    for _ in range(100):
        clock.now += 0.01
        st.publish()
    poses = client.of("sim.pose")
    assert 19 <= len(poses) <= 21
    p = poses[-1]
    assert set(p) == {"x", "y", "yaw", "v_mps", "steer_rad", "world_revision", "surface", "race"}
    assert p["surface"] == "road" and p["world_revision"] == st.revision
    assert (p["x"], p["y"]) == (round(sim.vehicle.x, 4), round(sim.vehicle.y, 4))


def test_envelopes_pass_contract_validation(setup):
    sim, client, st = setup
    st.publish()
    for topic, message_type, payload, _ in client.published:
        envelope = env.build(env.Identity(source_id=SOURCE_ID), message_type, payload, 1)
        env.validate(topic, envelope)
        assert len(json.dumps(envelope)) < 64 * 1024


def test_obstacle_add_move_remove_with_results(setup):
    sim, client, st = setup
    x, y = ahead(sim, 2.0)
    client.command({"op": "obstacle.add", "request_id": "r1", "obstacle_id": "o1", "kind": "tires", "x": x, "y": y})
    assert [o.kind for o in sim.vehicle.obstacles] == ["tires"]
    x2, y2 = ahead(sim, 3.0, 0.1)
    client.command({"op": "obstacle.move", "request_id": "r2", "obstacle_id": "o1", "x": x2, "y": y2})
    assert sim.vehicle.obstacles[0].x == pytest.approx(x2)
    client.command({"op": "obstacle.remove", "request_id": "r3", "obstacle_id": "o1"})
    assert sim.vehicle.obstacles == []
    results = client.of("sim.command.result")
    assert [(r["request_id"], r["ok"], r["code"]) for r in results] == [("r1", True, None), ("r2", True, None), ("r3", True, None)]
    assert set(results[0]) == {"request_id", "op", "ok", "code", "message", "gap_m", "need_m"}


def test_duplicate_add_is_ignored_not_doubled(setup):
    sim, client, _ = setup
    x, y = ahead(sim, 2.0)
    for _ in range(2):
        client.command({"op": "obstacle.add", "request_id": "r", "obstacle_id": "o1", "x": x, "y": y})
    assert len(sim.vehicle.obstacles) == 1 and all(r["ok"] for r in client.of("sim.command.result"))


@pytest.mark.parametrize("payload, code", [
    ({"op": "obstacle.add", "obstacle_id": "o1", "x": "a", "y": 0}, "INVALID"),
    ({"op": "obstacle.add", "obstacle_id": "o1", "kind": "rock", "x": 0, "y": 0}, "INVALID"),
    ({"op": "obstacle.move", "obstacle_id": "nope", "x": 0, "y": 0}, "INVALID"),
    ({"op": "obstacle.remove", "obstacle_id": "nope"}, "INVALID"),
    ({"op": "push", "dy_m": 0.8}, "INVALID"),
    ({"op": "estop", "engaged": "yes"}, "INVALID"),
    ({"op": "track.select", "track_id": "suzuka"}, "INVALID"),
    ({"op": "fly"}, "INVALID"),
])
def test_invalid_commands_are_refused(setup, payload, code):
    _, client, _ = setup
    client.command({"request_id": "bad", **payload})
    r = client.of("sim.command.result")[-1]
    assert r["ok"] is False and r["code"] == code and r["message"] and r["request_id"] == "bad"


def test_placement_refuses_on_car_and_out_of_track(setup):
    sim, client, _ = setup
    client.command({"op": "obstacle.add", "request_id": "a", "obstacle_id": "o1", "x": sim.vehicle.x, "y": sim.vehicle.y})
    client.command({"op": "obstacle.add", "request_id": "b", "obstacle_id": "o2", "x": 1000.0, "y": 1000.0})
    assert [r["code"] for r in client.of("sim.command.result")] == ["ON_CAR", "OUT_OF_TRACK"]
    assert sim.vehicle.obstacles == []


def at_start(sim, di, off):
    """출발선에서 중심선 점 di 개 앞, 옆 off (+ 왼쪽). 레드불링 출발 직선은 벽이 0.39 m (잔디 없음)."""
    T = sim.track
    return T.at((T.start_index + di) % T.N, off)


def add(client, oid, kind, xy):
    client.command({"op": "obstacle.add", "request_id": oid, "obstacle_id": oid, "kind": kind, "x": xy[0], "y": xy[1]})
    return client.of("sim.command.result")[-1]


def test_partly_outside_wall_is_out_of_track(setup):
    """mock 과 같이 장애물이 벽 밖으로 조금이라도 나가면 거부 (|off| > 벽 − 폭/2)."""
    sim, client, _ = setup
    assert add(client, "o1", "cone", at_start(sim, 30, 0.39 - 0.09 + 0.01))["code"] == "OUT_OF_TRACK"
    assert add(client, "o2", "cone", at_start(sim, 30, 0.39 - 0.09 - 0.01))["ok"] is True


def test_no_gap_reports_gap_and_need(setup):
    """폭 0.30 박스를 벽 0.39 m 직선 가운데 → 양쪽 틈 0.24 m < 필요 0.26 m (차 폭 0.20 + 양쪽 0.03)."""
    sim, client, _ = setup
    r = add(client, "b1", "box", at_start(sim, 30, 0.0))
    assert r["ok"] is False and r["code"] == "NO_GAP"
    assert r["gap_m"] == pytest.approx(0.24, abs=1e-3) and r["need_m"] == pytest.approx(0.26)
    assert "틈" in r["message"] and sim.vehicle.obstacles == []
    assert add(client, "c1", "cone", at_start(sim, 30, 0.0))["ok"] is True     # 콘(0.18)이면 틈 0.30 m


def test_no_gap_between_two_obstacles(setup):
    sim, client, _ = setup
    assert add(client, "a", "cone", at_start(sim, 30, 0.2))["ok"] is True
    r = add(client, "b", "cone", at_start(sim, 30, -0.2))
    assert r["code"] == "NO_GAP" and r["gap_m"] < 0.26
    assert [o.id for o in sim.vehicle.obstacles] == ["a"]


def test_too_dense_slalom_is_refused(setup):
    """틈은 각각 있지만 0.6 m 뒤에서 반대쪽을 막으면 기준 회피 주행이 옮겨 갈 거리가 없다. 1.6 m 뒤면 지나간다."""
    sim, client, _ = setup
    assert add(client, "a", "cone", at_start(sim, 20, 0.15))["ok"] is True
    r = add(client, "b", "cone", at_start(sim, 23, -0.15))
    assert r["code"] == "TOO_DENSE" and r["gap_m"] is None and "촘촘" in r["message"]
    assert [o.id for o in sim.vehicle.obstacles] == ["a"]
    assert add(client, "c", "cone", at_start(sim, 28, -0.15))["ok"] is True


def test_too_close_only_after_start_and_in_front_of_running_car(setup, clock):
    sim, client, _ = setup
    sim.vehicle.wheel_rad_s = 1.0 / sim._settings.drivetrain.wheel_radius_m    # 1 m/s 로 달리는 중
    assert sim.race.phase == "lights"
    assert add(client, "x1", "cone", at_start(sim, 4, 0.0))["ok"] is True      # 출발 전: 지금 차로는 돌려 보지 않는다
    client.command({"op": "obstacle.remove", "request_id": "rm", "obstacle_id": "x1"})
    clock.now += 10.0
    sim.race.update(sim.track.start_index, sim.track.start_index, sim.track.N, manual=False, forward=False)
    assert sim.race.phase == "go"
    assert add(client, "x2", "cone", at_start(sim, 4, 0.0))["code"] == "TOO_CLOSE"
    assert add(client, "x3", "cone", at_start(sim, 40, 0.0))["ok"] is True     # 멀면 피할 거리가 있다


def test_rejected_move_keeps_old_place(setup):
    sim, client, _ = setup
    assert add(client, "a", "cone", at_start(sim, 30, 0.2))["ok"] is True
    old = sim.vehicle.obstacles[0]
    add(client, "b", "cone", at_start(sim, 60, -0.2))
    x, y = at_start(sim, 30, -0.2)
    client.command({"op": "obstacle.move", "request_id": "mv", "obstacle_id": "b", "x": x, "y": y})
    assert client.of("sim.command.result")[-1]["code"] == "NO_GAP"
    assert sim.vehicle.obstacles[0] == old and sim.vehicle.obstacles[1].x != x


def test_pose_carries_race(setup, clock):
    sim, client, st = setup
    st.publish()
    race = client.of("sim.pose")[-1]["race"]
    assert set(race) == {"phase", "lights_on", "lap", "lap_ms", "last_lap_ms", "best_lap_ms", "jump_start"}
    assert race["phase"] == "lights" and race["lap"] == 0 and race["last_lap_ms"] is None


def test_ninth_obstacle_is_refused(setup):
    sim, client, _ = setup
    for k in range(9):
        x, y = ahead(sim, 1.0 + 0.5 * k)
        client.command({"op": "obstacle.add", "request_id": f"r{k}", "obstacle_id": f"o{k}", "x": x, "y": y})
    assert len(sim.vehicle.obstacles) == 8
    assert client.of("sim.command.result")[-1]["code"] == "INVALID"


def test_estop_push_track_select_race_start(setup):
    sim, client, st = setup
    client.command({"op": "estop", "request_id": "e", "engaged": True})
    assert sim.estop is True
    before = (sim.vehicle.x, sim.vehicle.y)
    client.command({"op": "push", "request_id": "p", "dy_m": 0.3})
    assert math.hypot(sim.vehicle.x - before[0], sim.vehicle.y - before[1]) == pytest.approx(0.3)
    _, off, _ = sim.track_frame()
    assert off == pytest.approx(0.3, abs=1e-6)  # + 왼쪽
    client.command({"op": "race.start", "request_id": "s"})
    assert (sim.vehicle.x, sim.vehicle.y) == sim.track.start_pose()[:2]
    sim.add_obstacle_ahead(2.0)
    client.command({"op": "track.select", "request_id": "t", "track_id": "monza"})
    assert sim.track.id == "monza" and sim.vehicle.obstacles == []
    st.publish()
    assert client.of("sim.world")[-1]["track"]["id"] == "monza"
    assert all(r["ok"] for r in client.of("sim.command.result"))


def test_clear_removes_retained_world(setup):
    _, client, st = setup
    st.clear()
    assert client.raw == [(topics.sim_world(), b"", True)]
