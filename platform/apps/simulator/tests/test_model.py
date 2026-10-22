from __future__ import annotations

import math

import pytest

from xenova_simulator.config import SimSettings
from xenova_simulator.model import Obstacle, Vehicle


@pytest.fixture
def car():
    return Vehicle(SimSettings())


def run(car, v, steer, seconds, dt=0.01):
    for _ in range(round(seconds / dt)):
        car.step(v, steer, dt)


def test_speed_follows_target_with_accel_limit(car):
    run(car, 1.0, 0.0, 0.25)
    assert car.v_mps == pytest.approx(0.5, abs=1e-6)  # 2 m/s² × 0.25 s
    run(car, 1.0, 0.0, 1.0)
    assert car.v_mps == pytest.approx(1.0)


def test_straight_line_moves_along_heading(car):
    run(car, 1.0, 0.0, 2.0)
    assert car.x > 1.0 and car.y == pytest.approx(0.0)
    assert car.heading_rad == pytest.approx(0.0)


def test_left_steer_turns_counterclockwise(car):
    run(car, 1.0, 0.3, 1.0)
    assert car.heading_rad > 0 and car.y > 0


def test_steer_is_rate_limited_and_clamped(car):
    car.step(0.0, 1.0, 0.05)
    assert car.steer_rad == pytest.approx(0.15)  # 3 rad/s × 0.05 s
    run(car, 0.0, 1.0, 1.0)
    assert car.steer_rad == pytest.approx(SimSettings().max_steer_rad)


def test_obstacle_ahead_blocks_forward_but_allows_reverse(car):
    car.place_obstacle_ahead(0.4)
    assert car.obstacle_ahead()
    run(car, 1.0, 0.0, 0.5)
    assert car.v_mps == 0.0 and car.x == pytest.approx(0.0)
    run(car, -0.5, 0.0, 0.5)
    assert car.v_mps < 0


def test_moving_car_stops_immediately_when_obstacle_enters_range(car):
    run(car, 1.0, 0.0, 1.0)
    car.place_obstacle_ahead(0.55)  # 가장자리 0.45 m — 감지 범위 0.5 m 안
    car.step(1.0, 0.0, 0.01)
    assert car.v_mps == 0.0


def test_obstacle_behind_or_far_is_ignored(car):
    car.obstacles.append(Obstacle(-0.3, 0.0))
    assert not car.obstacle_ahead()
    car.obstacles.clear()
    car.place_obstacle_ahead(2.0)
    assert not car.obstacle_ahead()


def test_obstacle_outside_cone_is_ignored(car):
    car.heading_rad = math.pi / 2  # 북쪽을 봄
    car.obstacles.clear()
    car.place_obstacle_ahead(0.3)
    car.heading_rad = 0.0  # 장애물은 이제 왼쪽 90°
    assert not car.obstacle_ahead()


def test_reset(car):
    run(car, 1.0, 0.2, 1.0)
    car.reset()
    assert (car.x, car.y, car.heading_rad, car.v_mps, car.steer_rad) == (0, 0, 0, 0, 0)
