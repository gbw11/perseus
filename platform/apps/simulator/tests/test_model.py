from __future__ import annotations

import math

import pytest

from xenova_simulator.config import Drivetrain, SimSettings
from xenova_simulator.model import Obstacle, Vehicle


@pytest.fixture
def car():
    return Vehicle(SimSettings())


def run(car, v, steer, seconds, dt=0.01):
    for _ in range(round(seconds / dt)):
        car.step(v, steer, dt)


def test_target_speed_becomes_wheel_rpm(car):
    run(car, 0.5, 0.0, 2.0)
    expected_rpm = 0.5 / (2 * math.pi * 0.033) * 60  # ≈144.7
    assert car.wheel_rpm == pytest.approx(expected_rpm, rel=0.01)
    assert car.v_mps == pytest.approx(0.5, rel=0.01)


def test_encoder_counts_wheel_turns_and_matches_distance(car):
    run(car, 0.5, 0.0, 3.0)
    d = car.settings.drivetrain
    turns = car.x / (2 * math.pi * d.wheel_radius_m)
    assert car.encoder_count == pytest.approx(turns * d.encoder_cpr, rel=0.01)
    assert isinstance(car.encoder_count, int)


def test_acceleration_is_limited(car):
    run(car, 1.5, 0.0, 0.1)
    assert car.v_mps <= 2.0 * 0.1 + 1e-9  # 2 m/s² 한계


def test_motor_lags_target(car):
    car.step(1.0, 0.0, 0.01)
    assert 0 < car.v_mps < 1.0
    assert car.target_rad_s == pytest.approx(1.0 / 0.033)


def test_wheel_rpm_is_capped(car):
    run(car, 10.0, 0.0, 5.0)
    assert car.wheel_rpm == pytest.approx(car.settings.drivetrain.max_wheel_rpm, rel=1e-6)


def test_stop_target_brings_wheel_to_rest(car):
    run(car, 1.0, 0.0, 2.0)
    run(car, 0.0, 0.0, 1.0)
    assert car.wheel_rad_s == 0.0 and car.v_mps == 0.0


def test_reverse_turns_wheel_backwards(car):
    run(car, -0.3, 0.0, 2.0)
    assert car.wheel_rpm < 0 and car.x < 0 and car.encoder_count < 0


def test_constant_steer_gives_bicycle_turn_radius(car):
    delta = 0.3
    run(car, 0.5, delta, 1.0)  # 속도·서보가 자리 잡게
    xs = []
    for _ in range(800):  # 0.5 m/s 로 8 s → 한 바퀴(≈5.6 m) 넘게
        car.step(0.5, delta, 0.01)
        xs.append(car.x)
    radius = (max(xs) - min(xs)) / 2
    assert radius == pytest.approx(car.settings.wheelbase_m / math.tan(delta), rel=0.03)


def test_left_steer_turns_counterclockwise(car):
    run(car, 1.0, 0.3, 1.0)
    assert car.heading_rad > 0 and car.y > 0


def test_servo_is_rate_limited_and_clamped(car):
    car.step(0.0, 1.0, 0.05)
    assert car.steer_rad == pytest.approx(5.0 * 0.05)  # 5 rad/s × 0.05 s
    run(car, 0.0, 1.0, 1.0)
    assert car.steer_rad == pytest.approx(Drivetrain().max_steer_rad)


def test_steering_without_wheel_turn_does_not_move_car(car):
    run(car, 0.0, 0.4, 1.0)
    assert (car.x, car.y, car.heading_rad) == (0.0, 0.0, 0.0)


def test_obstacle_ahead_blocks_forward_but_allows_reverse(car):
    car.place_obstacle_ahead(0.6)  # 앞바퀴 축(0.26 m)에서 가장자리까지 0.24 m
    assert car.obstacle_ahead()
    run(car, 1.0, 0.0, 0.5)
    assert car.v_mps == 0.0 and car.x == pytest.approx(0.0)
    run(car, -0.5, 0.0, 0.5)
    assert car.v_mps < 0


def test_moving_car_stops_immediately_when_obstacle_enters_range(car):
    run(car, 1.0, 0.0, 1.0)
    car.place_obstacle_ahead(0.26 + 0.1 + 0.45)  # 가장자리가 앞바퀴 축에서 0.45 m
    car.step(1.0, 0.0, 0.01)
    assert car.v_mps == 0.0


def test_obstacle_behind_or_far_is_ignored(car):
    car.obstacles = [Obstacle(-0.3, 0.0)]
    assert not car.obstacle_ahead()
    car.obstacles = []
    car.place_obstacle_ahead(2.0)
    assert not car.obstacle_ahead()


def test_obstacle_outside_cone_is_ignored(car):
    car.obstacles = [Obstacle(0.26, 0.4)]  # 앞바퀴 축 바로 왼쪽
    assert not car.obstacle_ahead()


def test_reset_places_car(car):
    run(car, 1.0, 0.2, 1.0)
    car.reset(1.0, -1.5, 0.5)
    assert (car.x, car.y, car.heading_rad, car.v_mps, car.steer_rad) == (1.0, -1.5, 0.5, 0.0, 0.0)
