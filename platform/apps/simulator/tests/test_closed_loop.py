"""카메라 화면만으로 트랙을 도는지 (렌더 → 인지 → 자율 계산 대역 → 시리얼 프레임 → 바퀴). OpenCV 가 있을 때만."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("cv2")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import closed_loop  # noqa: E402
from xenova_simulator.config import SimSettings  # noqa: E402


def test_auto_target_matches_zephyr_rules():
    cfg = closed_loop.AutoCfg()
    clear = {"lane_offset_m": 0.1, "heading_err_rad": 0.0, "obstacles": [], "stop": False}
    v, steer, reason = closed_loop.auto_target(cfg, clear)
    assert steer == pytest.approx(-0.15) and reason == "cruise"
    assert v == pytest.approx(0.5 * (1 - 0.4 * 0.15 / 0.4))
    near = {**clear, "obstacles": [{"x": 1.2, "y": 0.0, "w": 0.2}]}
    assert closed_loop.auto_target(cfg, near)[2] == "slowing"
    assert closed_loop.auto_target(cfg, {**clear, "stop": True})[:3:2] == (0.0, "obstacle_stop")


def test_drives_inside_the_lane_through_corners():
    # 레드불링 출발선에서 40 s (≈19 m, 첫 코너들 포함). 10개 트랙 전체 한 바퀴는 tools/closed_loop.py --all-tracks --laps 1
    r = closed_loop.run(40.0, settings=SimSettings(camera_listen=None, track_id="spielberg"))
    assert r.distance_m > 15.0
    assert r.out_of_lane_ticks == 0                  # 차로(±0.3 m) 밖으로 나간 적 없음
    assert r.max_abs_offset_m < 0.2
    assert r.reasons.get("cruise", 0) > 0.9 * sum(r.reasons.values())


def test_stops_before_obstacle_on_straight():
    r = closed_loop.run(12.0, obstacle_ahead_m=2.0)
    assert r.stopped_gap_m is not None and r.stopped_gap_m >= 0.3
    assert r.reasons.get("obstacle_stop", 0) > 0
