"""공유 코스의 차량 가정·초기 배치·전환·실제 world envelope 연결을 확인한다."""

import json
import math

import pytest

from xenova_mqtt import envelope
from xenova_simulator.config import SimSettings
from xenova_simulator.sim import Simulator
from xenova_simulator.simtopics import SimTopics
from xenova_simulator.track import track_ids


@pytest.mark.parametrize("track_id", [t for t in track_ids() if t.startswith("jetson-")])
def test_demo_profile_seeds_and_full_world_envelope(track_id):
    sim = Simulator(object(), SimSettings(track_id=track_id, camera_listen=None))
    settings = sim.settings
    assert settings.wheelbase_m / math.tan(settings.drivetrain.max_steer_rad) == pytest.approx(.55)
    assert settings.camera.height_m == .12
    assert math.degrees(settings.camera.hfov_rad) == pytest.approx(69)
    assert (sim.vehicle.x, sim.vehicle.y, sim.vehicle.heading_rad) == tuple(sim.track.layout["start_pose"])
    assert [o.id for o in sim.vehicle.obstacles] == ["A", "B"]
    assert sim.vehicle.obstacles[0].x > sim.vehicle.obstacles[1].x
    assert sim.vehicle.obstacles[0].y > sim.vehicle.obstacles[1].y
    # 앞범퍼를 출발선에 놓는다. 18cm 콘까지의 간격 검사는 회피 가능성 검사가 아니다.
    first = sim.vehicle.obstacles[0]
    vehicle = sim.track.layout["vehicle"]
    front_offset = (vehicle["length_m"] + vehicle["wheelbase_m"]) / 2
    assert sim.vehicle.x - front_offset - first.x - first.w / 2 == pytest.approx(.55)
    assert not sim.vehicle.obstacle_ahead()
    body = SimTopics(sim, object()).world()
    assert body["car"] == {"length_m": .30, "width_m": .22}
    assert body["track"]["layout"]["lane_count"] == 2
    wire = envelope.build(envelope.Identity(source_id="simulator"), "sim.world", body, 1)
    assert len(json.dumps(wire, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= 65536


def test_track_switch_restores_legacy_profile_and_clear_is_preserved_on_restart():
    base = SimSettings(camera_listen=None)
    sim = Simulator(object(), base)
    assert not sim.vehicle.obstacles
    sim.select_track("jetson-demo")
    assert len(sim.vehicle.obstacles) == 2
    assert sim.settings.camera.height_m == .12
    sim.clear_obstacles()
    sim.reset_to_start()
    assert not sim.vehicle.obstacles
    sim.select_track("spielberg")
    assert sim.settings == base
    assert not sim.vehicle.obstacles
    assert "layout" not in sim.track.payload()


def test_parking_surface_is_road_in_pose_output():
    sim = Simulator(object(), SimSettings(track_id="jetson-demo", camera_listen=None))
    slot = sim.track.layout["parking_slots"][1]["outline"]
    sim.vehicle.x = sum(p[0] for p in slot) / len(slot)
    sim.vehicle.y = sum(p[1] for p in slot) / len(slot)
    assert SimTopics(sim, object()).pose()["surface"] == "road"
