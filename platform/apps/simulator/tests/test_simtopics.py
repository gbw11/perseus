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
    assert len(world["tracks"]) == 10 and world["car"] == {"length_m": 0.42, "width_m": 0.2}
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
    assert set(p) == {"x", "y", "yaw", "v_mps", "steer_rad", "world_revision", "surface"}
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
