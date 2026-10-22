from __future__ import annotations

import math

import pytest

from xenova_perception.config import Camera, StopZone
from xenova_perception.geometry import Lane, Obstacle, Projector, in_stop_zone, make_result, obstacle_from_box, payload

W, H = 640, 480


@pytest.fixture
def projector():
    return Projector(Camera(height_m=0.15, pitch_rad=math.radians(15), hfov_rad=math.radians(60)), W, H)


def test_bottom_centre_pixel_is_ahead_on_centre_line(projector):
    x, y = projector.to_ground(W / 2, H - 1)
    assert x > 0
    assert y == pytest.approx(0.0, abs=1e-9)


def test_optical_axis_hits_ground_at_height_over_tan_pitch(projector):
    # 화면 중심 광선은 pitch 만큼 숙어 있으므로 x = h / tan(pitch)
    x, y = projector.to_ground(W / 2, H / 2)
    assert x == pytest.approx(0.15 / math.tan(math.radians(15)))
    assert y == pytest.approx(0.0, abs=1e-9)


def test_left_of_image_is_positive_y(projector):
    _, y_left = projector.to_ground(W * 0.1, H - 1)
    _, y_right = projector.to_ground(W * 0.9, H - 1)
    assert y_left > 0 > y_right
    assert y_left == pytest.approx(-y_right)


def test_lower_rows_are_closer(projector):
    far = projector.to_ground(W / 2, H * 0.6)[0]
    near = projector.to_ground(W / 2, H * 0.95)[0]
    assert near < far


def test_above_horizon_is_not_ground(projector):
    hv = projector.horizon_v()
    assert projector.to_ground(W / 2, hv - 5) is None
    assert projector.to_ground(W / 2, hv + 5) is not None


@pytest.mark.parametrize("x, y", [(0.4, 0.0), (1.0, 0.2), (2.0, -0.3), (0.8, 0.1)])
def test_image_and_ground_round_trip(projector, x, y):
    u, v = projector.to_image(x, y)
    gx, gy = projector.to_ground(u, v)
    assert (gx, gy) == pytest.approx((x, y))


def test_obstacle_from_box_width_matches_ground_width(projector):
    # 1 m 앞, 폭 0.3 m 물체의 아랫변 양 끝을 화면에 찍어 박스를 만들고 되돌린다
    ul, vb = projector.to_image(1.0, 0.15)
    ur, _ = projector.to_image(1.0, -0.15)
    o = obstacle_from_box(projector, ul, ur, vb)
    assert o.x == pytest.approx(1.0)
    assert o.y == pytest.approx(0.0, abs=1e-9)
    assert o.w == pytest.approx(0.3)


def test_obstacle_box_above_horizon_is_dropped(projector):
    hv = projector.horizon_v()
    assert obstacle_from_box(projector, 100, 200, hv - 10) is None


ZONE = StopZone(distance_m=0.8, vehicle_width_m=0.2, margin_m=0.05)  # 좌우 0.15 m


@pytest.mark.parametrize(
    "obstacle, stop",
    [
        (Obstacle(0.5, 0.0, 0.2), True),     # 바로 앞
        (Obstacle(0.79, 0.0, 0.2), True),
        (Obstacle(0.8, 0.0, 0.2), False),    # 경계: x < distance
        (Obstacle(1.5, 0.0, 0.2), False),    # 멀다
        (Obstacle(0.5, 0.6, 0.2), False),    # 옆
        (Obstacle(0.5, 0.24, 0.2), True),    # 가장자리 0.14 가 0.15 안에 걸침
        (Obstacle(0.5, -0.26, 0.2), False),  # 가장자리 0.16
        (Obstacle(-0.1, 0.0, 0.2), False),   # 뒤
    ],
)
def test_stop_zone(obstacle, stop):
    assert in_stop_zone(obstacle, ZONE) is stop


def test_make_result_sorts_limits_and_judges_stop_on_all():
    obstacles = [Obstacle(2.0, 0, 0.2), Obstacle(0.5, 0, 0.2), Obstacle(1.0, 0.5, 0.2)]
    r = make_result(Lane(), obstacles, ZONE, at=1.0, limit=1)
    assert [o.x for o in r.obstacles] == [0.5]
    assert r.stop is True
    r = make_result(Lane(), [Obstacle(2.0, 0, 0.2), Obstacle(0.5, 0, 0.2)], ZONE, at=1.0, limit=0)
    assert r.obstacles == () and r.stop is True


def test_payload_has_exactly_contract_fields_and_types():
    r = make_result(Lane(0.0123456, -0.2), [Obstacle(1, 0, 0), Obstacle(0.5, -0.1, 0.25)], ZONE, at=0)
    body = payload(r)
    assert set(body) == {"lane_offset_m", "heading_err_rad", "obstacles", "stop"}
    assert type(body["lane_offset_m"]) is float and body["lane_offset_m"] == 0.012
    assert type(body["heading_err_rad"]) is float
    assert type(body["stop"]) is bool and body["stop"] is True
    assert isinstance(body["obstacles"], list)
    for o in body["obstacles"]:
        assert set(o) == {"x", "y", "w"}
        assert all(type(v) is float for v in o.values())
    assert body["obstacles"][0] == {"x": 0.5, "y": -0.1, "w": 0.25}


def test_payload_never_emits_nan():
    body = payload(make_result(Lane(float("nan"), float("inf")), [], ZONE, at=0))
    assert body["lane_offset_m"] == 0.0 and body["heading_err_rad"] == 0.0
