"""공유 데모 geometry, 전체 footprint 판정과 기존 10개 payload 회귀를 확인한다."""

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import runpy

import pytest

from xenova_simulator import placement
from xenova_simulator.track import Track, load, point_in_polygon, sources, track_ids

REPO = Path(__file__).resolve().parents[4]
GENERATOR = REPO / "platform/apps/simulator/tools/generate_jetson_tracks.py"
CANONICAL = REPO / "frontend/packages/ui/src/sim/jetson-tracks.json"
RADII = {"jetson-demo": .60, "jetson-r45": .45, "jetson-r55": .55,
         "jetson-r65": .65, "jetson-r75": .75, "jetson-r80": .80}
# 변경 전 구현으로 채취한 canonical JSON payload SHA256. 새 geometry가 기존 벽 계산을 바꾸면 실패한다.
LEGACY_PAYLOADS = {
    "spielberg": "5754bf12eb68ad1409c71c334f117765925a09154f8a87c7aa37dc442bc0ad8a",
    "monza": "331ddbdee93f6f4a75a961ced66a12ce78c72a964461f8bd4c5d797f2ced5237",
    "silverstone": "dafad779587e0f5ec5e0850a95cc2234423f6ba7cf16d2369cda9bd318700f04",
    "spa": "f94a6891286afb2c0f55596bd22c830fc69e3bd213f82c71b89316988f3d0bfb",
    "melbourne": "cb05c57dda60d2a4db27297116d61fa126f0813f419a41882b41c66c986bb550",
    "hungaroring": "4e9f9675feba278af7ed1b44dfcbb55ae5b9ad43d67ae09d02849d4bd4e8e8ae",
    "catalunya": "ed01ab894902e3786e3c9725b1c90fc743eec47d05c75ff3f4fe13ce723a3187",
    "mexico": "5e7c652a69a842c4ee378ef670a8658e6a94cead871bce21a7a5ffb7533c60bc",
    "bahrain": "b34e535ae705bc43227d546663de3ca5c00f677842f5b2799e49d08c8e6e314b",
    "interlagos": "cb89002b4363591f597654b4d2ab0f1c5628723326b2be569bc8e91ff1050949",
}


def test_canonical_geometry_is_reproducible():
    generator = runpy.run_path(str(GENERATOR))
    assert CANONICAL.read_bytes() == generator["generated_bytes"]()


@pytest.mark.parametrize("track_id,digest", LEGACY_PAYLOADS.items())
def test_original_payload_bytes_are_unchanged(track_id, digest):
    payload = json.dumps(load(track_id).payload(), sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(payload).hexdigest() == digest


@pytest.mark.parametrize("track_id,radius", RADII.items())
def test_course_dimensions_direction_and_start(track_id, radius):
    track = load(track_id)
    assert track.layout["bounds"] == [0, 0, 4.5, 3.0]
    assert (track.lane_width_m, track.curb_m, track.road_m) == (.8, 0, .4)
    assert track.C[0] == (2.56, .5)
    assert track.start_pose() == pytest.approx((2.81, .7, math.pi))
    car = placement.Car(*track.start_pose(), 0, 0, track.start_index)
    body = placement.car_polygon(track, car)
    assert min(p[0] for p in body) == pytest.approx(track.C[0][0])
    assert track.polygon_is_paved(body)
    assert track.start_index == 0 and track.tan[0][0] == pytest.approx(-1)
    assert sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(track.C, track.C[1:] + track.C[:1])) < 0
    assert max(math.dist(a, track.C[(i + 1) % track.N]) for i, a in enumerate(track.C)) <= .050002
    assert min(math.dist(a, track.C[(i + 1) % track.N]) for i, a in enumerate(track.C)) > 1e-6
    edges = [track.at(i, off) for i in range(track.N) for off in (-.4, .4)]
    assert all(math.isfinite(v) for p in edges for v in p)
    assert [min(p[0] for p in edges), min(p[1] for p in edges),
            max(p[0] for p in edges), max(p[1] for p in edges)] == pytest.approx([.1, .1, 4.4, 2.9], abs=.001)
    # 기존 0.2 m 간격 가정 대신 실제 sample 간 거리를 사용한 곡률이어야 한다.
    assert min(track.turn) == pytest.approx(-1 / (radius + .2), abs=.003)
    assert type(track.real_m) is int
    assert track.paved_at(2, .7)
    assert [(o["x"], o["y"]) for o in track.layout["obstacle_seeds"]] == [(1.92, .7), (1.4, .3)]
    assert all(track.box_is_paved(o["x"], o["y"], o["w"]) for o in track.layout["obstacle_seeds"])


@pytest.mark.parametrize("track_id", RADII)
def test_slots_apron_and_world_payload(track_id):
    track = load(track_id)
    slots = track.layout["parking_slots"]
    mouth, back = (1.2, 1.8) if RADII[track_id] > .65 else (1.1, 1.7)
    road_layout = deepcopy(track.layout)
    road_layout["paved_areas"] = []
    road_only = Track(replace(sources()[track_id], layout=road_layout))
    assert [slot["id"] for slot in slots] == ["P1", "P2"]
    left, right = slots[0]["outline"][0][0], slots[1]["outline"][2][0]
    assert (left, right) == (2.7, 3.5)
    for slot in slots:
        a, b, c, d = slot["outline"]
        assert [a[1], b[1], c[1], d[1]] == [mouth, back, back, mouth]
        assert all(not road_only.paved_at(*point) for point in slot["outline"])
        assert c[0] - b[0] == pytest.approx(.4)
        assert track.box_is_paved((a[0] + c[0]) / 2, 1.4, .18)
    assert track.box_is_paved((left + right) / 2, mouth, .18)  # 두 slot과 apron의 경계를 가로지른다.
    assert track.box_is_paved((left + right) / 2, .91, .18)  # 도로와 apron의 .015 m 겹침.
    assert not track.box_is_paved(left - .02, 1.4, .18)
    assert track.surface_at((left + right) / 2, 1.4) == "road"
    assert track.surface_at(left - .2, 1.4) == "grass"
    assert track.bounds(0) == pytest.approx(track.layout["bounds"])
    payload = track.payload()
    assert payload["layout"] == track.layout and payload["layout"] is not track.layout
    assert payload["centerline"] == [list(p) for p in track.C]
    envelope = {"schema_version": 1, "type": "sim.world", "payload": {
        "revision": 1, "track": payload, "tracks": [{"id": i, "name": load(i).name, "real_m": load(i).real_m} for i in track_ids()],
        "car": track.layout["vehicle"], "time_scale": 1, "obstacles": track.layout["obstacle_seeds"], "estop": False}}
    assert len(json.dumps(envelope, ensure_ascii=False).encode()) < 64 * 1024


def test_whole_footprint_does_not_accept_an_enclosed_grass_hole():
    track = load("jetson-demo")
    corners = [(.6, .6), (3.9, .6), (3.9, 2.4), (.6, 2.4)]
    assert all(track.paved_at(*p) for p in corners)
    assert not track.polygon_is_paved(corners)


def test_paved_point_includes_boundary_for_both_windings():
    polygon = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for shape in (polygon, polygon[::-1]):
        assert point_in_polygon(0, .5, shape)
        assert point_in_polygon(.5, .5, shape)
        assert not point_in_polygon(-.001, .5, shape)


def test_demo_placement_uses_full_box_and_rear_axle_body():
    track = load("jetson-demo")
    x, y, yaw = track.start_pose()
    car = placement.Car(x, y, yaw, 0, 0, 0)
    body = placement.car_polygon(track, car)
    assert max(p[0] for p in body) == pytest.approx(x + .05)
    assert min(p[0] for p in body) == pytest.approx(x - .25)
    near_nose = placement.Item.at(track, "nose", "cone", x - .27, y)
    assert placement.check(track, [near_nose], near_nose, car, False).code == "ON_CAR"
    slot = track.layout["parking_slots"][0]["outline"]
    ox = (slot[0][0] + slot[2][0]) / 2
    parked = placement.Item.at(track, "parked", "cone", ox, 1.4)
    assert placement.check(track, [parked], parked, car, False) is None
    outside = placement.Item.at(track, "outside", "cone", slot[0][0] - .02, 1.4)
    assert placement.check(track, [outside], outside, car, False).code == "OUT_OF_TRACK"


@pytest.mark.parametrize("yaw", [0, math.pi / 2, math.pi, 3 * math.pi / 4])
def test_demo_contact_uses_rotated_body(yaw):
    track = load("jetson-demo")
    car = placement.Car(2, .5, yaw, 0, 0, 0)
    c, s = math.cos(yaw), math.sin(yaw)
    nose = placement.Item.at(track, "nose", "cone", car.x + .30 * c, car.y + .30 * s)
    behind = placement.Item.at(track, "behind", "cone", car.x - .20 * c, car.y - .20 * s)
    assert placement.touching(track, car, [nose])
    assert not placement.touching(track, car, [behind])
