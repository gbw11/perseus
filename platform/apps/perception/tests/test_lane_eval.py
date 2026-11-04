"""시뮬레이터 전방 카메라 화면에서 차선 추정이 트랙 정답과 맞는지 (S15P31E110-320). OpenCV·시뮬레이터가 있을 때만.

CI 에서는 급코너(한쪽 선만 보이는 곳)가 있는 트랙 몇 개의 짧은 구간만 본다.
10개 트랙 한 바퀴 전체는 tools/lane_eval.py, 폐루프는 simulator/tools/closed_loop.py --all-tracks --laps 1.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

pytest.importorskip("cv2")
pytest.importorskip("xenova_simulator")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import lane_eval  # noqa: E402
from xenova_simulator.camera import Renderer  # noqa: E402
from xenova_simulator.config import CameraSpec  # noqa: E402
from xenova_simulator.track import load  # noqa: E402

# 목표 (-320): heading 오차 p95 ≤ 0.15 rad. 고치기 전(2026-11-03) 이 구간들에서 heading p95 0.4 rad 안팎, offset p95 0.2 m 안팎
HEADING_P95_RAD = 0.15
OFFSET_P95_M = 0.06


@pytest.fixture(scope="module")
def renderer():
    return Renderer(CameraSpec())


def sharpest_window(track_id: str, before_m: float = 4.0, length_m: float = 10.0) -> tuple[float, float]:
    """가장 급한 코너 before_m 앞부터 length_m. (출발선부터 거리, 길이)."""
    t = load(track_id)
    i = max(range(t.N), key=lambda k: abs(t.turn[k]))
    return (t.SL[i] - t.SL[t.start_index] - before_m) % t.lap_m, length_m


def test_ground_truth_on_start_straight_is_zero():
    t = load("spielberg")
    x, y, h = t.start_pose()
    offset, heading = lane_eval.ground_truth(t, x, y, h)
    assert offset == pytest.approx(0.0, abs=1e-3) and heading == pytest.approx(0.0, abs=0.03)  # 출발 직선도 조금 굽어 있다
    # 왼쪽으로 0.1 m 옮기면 차가 중심보다 왼쪽 → offset +0.1 (README "좌표계·단위")
    offset, _ = lane_eval.ground_truth(t, x - 0.1 * math.sin(h), y + 0.1 * math.cos(h), h)
    assert offset == pytest.approx(0.1, abs=2e-3)


def test_ground_truth_heading_turns_with_the_corner():
    t = load("spielberg")
    for sign in (1, -1):
        i = max(range(t.N), key=lambda k: sign * t.turn[k])
        _, heading = lane_eval.ground_truth(t, *t.C[i], math.atan2(t.tan[i][1], t.tan[i][0]))
        assert sign * heading > 0.1


@pytest.mark.parametrize("track_id", ["spielberg", "hungaroring", "melbourne"])
def test_lane_estimate_matches_ground_truth_through_sharpest_corner(renderer, track_id):
    start_m, length_m = sharpest_window(track_id)
    samples = lane_eval.evaluate_track(track_id, step_m=3 * lane_eval.FRAME_STEP_M, start_m=start_m,
                                       length_m=length_m, renderer=renderer)
    s = lane_eval.stats(samples)
    assert s.n > 90 and s.found == 1.0
    assert s.heading_p95 <= HEADING_P95_RAD, s
    assert s.offset_p95 <= OFFSET_P95_M, s
