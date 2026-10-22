"""계약 11장 시뮬레이터 전용 토픽: sim.pose·sim.world 발행, sim.command 적용 (S15P31E110-261)."""

from __future__ import annotations

import io
import math
import threading

import pytest

from test_sim import Clock, FakeClient, drive
from xenova_mqtt import Message, topics
from xenova_simulator.__main__ import handle, parse_args
from xenova_simulator.config import MAX_OBSTACLES, SimSettings
from xenova_simulator.sim import Simulator


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def client():
    return FakeClient()


@pytest.fixture
def sim_client():
    return FakeClient()


@pytest.fixture
def sim(client, sim_client, clock):
    s = Simulator(client, SimSettings(), clock=clock, sim_client=sim_client)
    s.subscribe()
    return s


def command(sim_client, **payload):
    topic = topics.sim_command()
    sim_client.handlers[topic](Message(topic, {"type": "sim.command", "payload": payload}))


def sim_published(sim_client, message_type):
    return [p for t, m, p in sim_client.published if m == message_type]


def step(sim, clock, seconds, dt=0.01):
    for _ in range(round(seconds / dt)):
        clock.now += dt
        sim.tick(dt)
        sim.publish_sim()


def test_subscribes_sim_command_on_sim_client(client, sim_client, sim):
    assert set(sim_client.handlers) == {topics.sim_command()}
    assert topics.sim_command() not in client.handlers


def test_starts_on_track_facing_counterclockwise(sim):
    v = sim.vehicle
    assert (v.x, v.y) == (3.0, 0.0) and v.heading_rad == pytest.approx(math.pi / 2)


def test_world_published_once_at_start_then_only_on_change(sim, sim_client, clock):
    step(sim, clock, 0.5)
    worlds = sim_published(sim_client, "sim.world")
    assert len(worlds) == 1
    w = worlds[0]
    assert w["revision"] == 1 and w["obstacles"] == [] and w["estop"] is False
    assert w["track"] == {"shape": "ellipse", "a_m": 3.0, "b_m": 1.5, "lane_width_m": 0.4}


def test_pose_20hz_with_world_revision(sim, sim_client, clock):
    step(sim, clock, 2.0)
    poses = sim_published(sim_client, "sim.pose")
    assert len(poses) in (40, 41)
    assert set(poses[-1]) == {"x", "y", "yaw", "v_mps", "steer_rad", "world_revision"}
    assert poses[-1]["x"] == 3.0 and poses[-1]["world_revision"] == 1


def test_obstacle_add_move_remove_bumps_revision(sim, sim_client, clock):
    step(sim, clock, 0.05)
    command(sim_client, op="obstacle.add", obstacle_id="ob-1", x=3.0, y=0.6, w=0.15, request_id="r1")
    step(sim, clock, 0.05)
    w = sim_published(sim_client, "sim.world")[-1]
    assert w["revision"] == 2 and w["obstacles"] == [{"id": "ob-1", "x": 3.0, "y": 0.6, "w": 0.15}]

    command(sim_client, op="obstacle.move", obstacle_id="ob-1", x=2.9, y=0.8, request_id="r2")
    command(sim_client, op="obstacle.remove", obstacle_id="ob-1", request_id="r3")
    step(sim, clock, 0.05)
    w = sim_published(sim_client, "sim.world")[-1]
    assert w["revision"] == 4 and w["obstacles"] == []


def test_added_obstacle_blocks_forward(sim, sim_client):
    command(sim_client, op="obstacle.add", obstacle_id="ob-1", x=3.0, y=0.4, w=0.15)
    assert sim.vehicle.obstacle_ahead()


def test_duplicate_add_is_ignored(sim, sim_client):
    command(sim_client, op="obstacle.add", obstacle_id="ob-1", x=3.0, y=0.6, w=0.15)
    command(sim_client, op="obstacle.add", obstacle_id="ob-1", x=1.0, y=1.0, w=0.15)
    assert [(o.x, o.y) for o in sim.vehicle.obstacles] == [(3.0, 0.6)] and sim.world_revision == 2


@pytest.mark.parametrize("payload, reason", [
    ({"op": "obstacle.add", "obstacle_id": "a", "x": 9.0, "y": 0.0, "w": 0.1}, "out_of_bounds"),
    ({"op": "obstacle.add", "obstacle_id": "a", "x": 0.0, "y": "1", "w": 0.1}, "out_of_bounds"),
    ({"op": "obstacle.add", "obstacle_id": "a", "x": 0.0, "y": 0.0, "w": 0}, "width"),
    ({"op": "obstacle.add", "obstacle_id": "", "x": 0.0, "y": 0.0}, "obstacle_id"),
    ({"op": "obstacle.move", "obstacle_id": "nope", "x": 0.0, "y": 0.0}, "not_found"),
    ({"op": "obstacle.remove", "obstacle_id": "nope"}, "not_found"),
    ({"op": "estop", "engaged": "yes"}, "engaged"),
    ({"op": "push", "dy_m": 0.6}, "dy_m"),
    ({"op": "teleport"}, "op"),
])
def test_invalid_commands_rejected(sim, payload, reason):
    assert sim.apply(payload) == reason
    assert sim.world_revision == 1


def test_ninth_obstacle_rejected(sim):
    for i in range(MAX_OBSTACLES):
        assert sim.apply({"op": "obstacle.add", "obstacle_id": f"o{i}", "x": -2.0, "y": 0.1 * i, "w": 0.1}) is None
    assert sim.apply({"op": "obstacle.add", "obstacle_id": "o9", "x": -2.0, "y": 1.0, "w": 0.1}) == "too_many"


def test_estop_command_sets_flag_and_world(sim, sim_client, clock):
    command(sim_client, op="estop", engaged=True)
    step(sim, clock, 0.06)
    assert sim.estop and sim.status()["flags"]["estop"] is True
    assert sim_published(sim_client, "sim.world")[-1]["estop"] is True
    command(sim_client, op="estop", engaged=True)  # 같은 값이면 revision 그대로
    assert sim.world_revision == 2


def test_push_moves_left_of_heading(sim, sim_client, clock):
    command(sim_client, op="push", dy_m=0.3)
    step(sim, clock, 0.01)
    # 시작 방향 +y 에서 왼쪽은 −x
    assert sim.vehicle.x == pytest.approx(2.7) and sim.vehicle.y == pytest.approx(0.0, abs=1e-9)
    assert sim.world_revision == 1  # 위치만 바뀐다


def test_no_sim_client_means_no_sim_topics(client, clock):
    s = Simulator(client, SimSettings(), clock=clock)
    s.subscribe()
    drive(s, clock, 0.5)
    s.publish_sim()
    assert {m for _, m, _ in client.published} == {"vehicle.status"}


def test_terminal_obstacle_and_push_update_world(sim):
    out, stop = io.StringIO(), threading.Event()
    handle("o 0.3", sim, stop, out)
    assert sim.vehicle.obstacles[0].id == "t1" and sim.world_revision == 2
    handle("c", sim, stop, out)
    assert sim.world_revision == 3
    handle("p 0.2", sim, stop, out)
    assert "차 밀기 +0.20" in out.getvalue()
    handle("p 0.9", sim, stop, out)
    assert "거부 (dy_m)" in out.getvalue()


def test_cli_sim_options():
    assert parse_args(["--sim-username", ""]).sim_username is None
    assert parse_args([]).sim_username == "simulator" and parse_args([]).sim_topics
    assert not parse_args(["--no-sim-topics"]).sim_topics
